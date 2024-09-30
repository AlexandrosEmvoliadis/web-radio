from flask import Flask, render_template, request, jsonify
from threading import Thread, Lock
from pydub import AudioSegment, generators
from pydub.utils import make_chunks
from queue import Queue, Empty
import os
import time
import numpy as np
import subprocess
import wave
from tinytag import TinyTag
from functools import reduce
from datetime import datetime
import json
import sounddevice as sd

app = Flask(__name__)

# Global variables and settings
is_playing = False
mic_volume = -90
music_volume = 0
fade_duration = 5
output_wav_path = "./stream_output.wav"
saved_wav_path = "./saved_show.wav"
liq_script_path = "./stream.liq"
lock = Lock()
buffer_duration_ms = 20
sample_rate = 44100
channels = 2
sample_width = 2
audio_queue = Queue(maxsize=10)
audio_folder = None
playlist = []
total_duration_seconds = 0
show_start_time = None
annotations_file_path = 'annotations.json'


@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

@app.route('/load-folder', methods=['POST'])
def load_folder():
    """ Return the valid tracks in directory."""
    folder_path = request.json.get('folderPath')
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({'status': 'error', 'message': 'Invalid folder path.'})

    files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.mp3', '.wav'))]
    return jsonify({'status': 'success', 'files': files, 'folderPath': folder_path})


@app.route('/list-files', methods=['GET'])
def list_files():
    """List all .wav files in the upload directory."""
    files = [f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.wav')]
    return jsonify({'files': files})

@app.route('/add-to-playlist', methods=['POST'])
def add_to_playlist():
    """ Method that adds tracks to the playlist."""
    global total_duration_seconds
    file_name = request.json.get('fileName')
    folder_path = request.json.get('folderPath')
    if not file_name or not folder_path:
        return jsonify({'status': 'error', 'message': 'Invalid file or folder path.'})

    full_path = os.path.join(folder_path, file_name)
    if not any(track['path'] == full_path for track in playlist):
        duration_seconds = get_track_duration(full_path)
        playlist.append({"path": full_path, "name": file_name, "duration": duration_seconds})
        total_duration_seconds += duration_seconds

    formatted_playlist = [
        {
            'name': os.path.basename(track['path']),
            'path': track['path'],
            'duration': format_duration(track['duration'])
        }
        for track in playlist
    ]

    return jsonify({
        'status': 'success',
        'playlist': formatted_playlist,
        'totalDuration': format_duration(total_duration_seconds),
        'rawDuration': total_duration_seconds
    })


@app.route('/get-playlist', methods=['GET'])
def get_playlist():
    """Retrieve the current playlist."""
    return jsonify({'playlist': playlist})

@app.route('/start-show', methods=['POST'])
def start_show():
    """Start the audio mixing and streaming process."""
    global is_playing, show_start_time
    with lock:
        if not is_playing:
            is_playing = True
            print("Show started, mixing audio.")
            if not os.path.exists(output_wav_path):
                os.mkfifo(output_wav_path)
            show_start_time = datetime.now()
            create_annotations_file()
            if len(playlist) > 0:
                first_track_path = playlist[0]['path']
                first_genre = get_genre(first_track_path)
                current_genre = first_genre
                log_annotation('music', {'genre': first_genre}, timestamp="00:00:00")
                print(f"Now playing: {playlist[0]['name']} - Genre: {first_genre}")
            print("Show started, mixing audio.")
            Thread(target=mix_audio, daemon=True).start()
            Thread(target=write_to_outputs, daemon=True).start()
            Thread(target=start_liquidsoap, daemon=True).start()
            # Thread(target=real_time_playback, daemon=True).start()  # Start real-time playback
            try:
                # time.sleep(1)
                subprocess.Popen(['ffplay', '-autoexit', '-nodisp', '-f', 's16le', '-ar', '44100', '-ac', '2', '-i', output_wav_path],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print("FFplay started for real-time monitoring.")
            except Exception as e:
                print(f"Error starting FFplay: {e}")

            return jsonify({'status': 'Show started.'})
        else:
            return jsonify({'status': 'Show is already running.'})

@app.route('/switch-to-voice', methods=['POST'])
def switch_to_voice():
    """Switch from music to voice with a crossfade."""
    log_annotation('transition')
    crossfade_volumes(fade_in_mic=True)
    log_annotation('speech')
    print("Switched to voice mode with crossfade.")
    return jsonify({'status': 'Switched to voice mode with crossfade.'})


@app.route('/switch-to-music', methods=['POST'])
def switch_to_music():
    """Switch from voice to music with a crossfade."""
    log_annotation('transition')
    crossfade_volumes(fade_in_mic=False)
    current_track = playlist[current_track_index]['path']
    genre = get_genre(current_track)
    log_annotation('music', {'genre': genre})
    print("Switched back to music mode with crossfade.")
    return jsonify({'status': 'Switched back to music mode with crossfade.'})

def create_annotations_file():
    """Create the annotations file at the start of the show."""
    global show_start_time
    try:
        # Initialize the JSON structure with show start time
        initial_data = {
            "start_time": str(show_start_time),
            "annotations": {}
        }
        with open(annotations_file_path, 'w') as json_file:
            json.dump(initial_data, json_file, indent=4)
        print(f"Annotations file created at {annotations_file_path}.")
    except Exception as e:
        print(f"Error creating annotations file: {e}")


def update_annotations_file(timestamp, annotation):
    """Update the annotations.json file with the new annotation."""
    try:
        # Read the existing data
        with open(annotations_file_path, 'r') as json_file:
            data = json.load(json_file)

        # Update the annotations section with the new event
        data['annotations'][timestamp] = annotation

        # Write the updated data back to the JSON file
        with open(annotations_file_path, 'w') as json_file:
            json.dump(data, json_file, indent=4)

        print(f"Annotation updated at {timestamp}: {annotation}")
    except Exception as e:
        print(f"Error updating annotations file: {e}")

def save_annotations_to_file():
    """Save the annotations to a JSON file."""
    try:
        with open('annotations.json', 'w') as json_file:
            json.dump(annotations, json_file, indent=4)
        print("Annotations saved to annotations.json.")
    except Exception as e:
        print(f"Error saving annotations: {e}")

def get_elapsed_time():
    global show_start_time
    if show_start_time is None:
        return "00:00:00"

    elapsed_seconds = int(time.time() - show_start_time)
    return time.strftime("%H:%M:%S", time.gmtime(elapsed_seconds))

def log_annotation(event_type, additional_info=None, timestamp=None):
    global show_start_time
    if timestamp is None:
        current_time = datetime.now()
        elapsed_time = (current_time - show_start_time).total_seconds()  # Calculate time since show started

        # Format the elapsed time as HH:MM:SS
        timestamp = str(datetime.utcfromtimestamp(elapsed_time).strftime('%H:%M:%S.%f')[:-3])  # Truncate to milliseconds

    annotation_entry = {
        "event": event_type
    }

    if additional_info:
        annotation_entry.update(additional_info)
    update_annotations_file(timestamp, annotation_entry)
    print(f"Annotation logged at {timestamp}: {annotation_entry}")

def crossfade_volumes(fade_in_mic):
    """Gradually adjust the volumes of the music and voice channels to create a crossfade effect with overlap."""
    global mic_volume, music_volume
    steps = int(fade_duration / 0.1)

    mic_start_dB = -90 if fade_in_mic else 0
    mic_end_dB = 0 if fade_in_mic else -90
    music_start_dB = 0 if fade_in_mic else -90
    music_end_dB = -90 if fade_in_mic else 0

    mic_start_amp = 10 ** (mic_start_dB / 20)
    mic_end_amp = 10 ** (mic_end_dB / 20)
    music_start_amp = 10 ** (music_start_dB / 20)
    music_end_amp = 10 ** (music_end_dB / 20)

    mic_amplitudes = np.linspace(mic_start_amp, mic_end_amp, steps)
    music_amplitudes = np.linspace(music_start_amp, music_end_amp, steps)

    mic_dB_values = 20 * np.log10(mic_amplitudes + 1e-10)
    music_dB_values = 20 * np.log10(music_amplitudes + 1e-10)

    for mic_dB, music_dB in zip(mic_dB_values, music_dB_values):
        with lock:
            mic_volume = mic_dB
            music_volume = music_dB
        print(f"Mic volume: {mic_volume:.1f} dB, Music volume: {music_volume:.1f} dB")
        time.sleep(0.1)
    print("Crossfade completed.")

def get_track_duration(file_path):
    """Retrieve the duration of a track in seconds."""
    try:
        tag = TinyTag.get(file_path)
        return tag.duration or 0
    except Exception as e:
        print(f"Error getting track duration: {e}")
        return 0

def get_genre(file_path):
    try:
        tag = TinyTag.get(file_path)
        return tag.genre or None
    except Exception as e:
        print(f"Error getting track genre: {e}")
        return 0

def format_duration(seconds):
    """Format the duration in seconds to HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def match_target_amplitude(sound, target_dBFS):
    """Adjust sound segment to match target dBFS."""
    change_in_dBFS = target_dBFS - sound.dBFS
    return sound.apply_gain(change_in_dBFS)

def sound_slice_normalize(sound, sample_rate, target_dBFS):
    """Normalize each chunk of sound to fall within target dBFS range."""
    def max_min_volume(min_dBFS, max_dBFS):
        for chunk in make_chunks(sound, sample_rate):
            if chunk.dBFS < min_dBFS:
                yield match_target_amplitude(chunk, min_dBFS)
            elif chunk.dBFS > max_dBFS:
                yield match_target_amplitude(chunk, max_dBFS)
            else:
                yield chunk
    return reduce(lambda x, y: x + y, max_min_volume(target_dBFS[0], target_dBFS[1]))

def mix_audio():
    """Continuously mix the music and simulated voice, adding the result to a queue."""
    global current_track_index, mic_volume, music_volume, is_playing,playlist
    current_track_index = 0
    current_genre = None

    while True:
        with lock:
            if not is_playing:
                break

        try:
            if current_track_index >= len(playlist):
                print("End of playlist.")
                with lock:
                    is_playing = False
                break

            with lock:
                track_path = playlist[current_track_index]['path']
                music_segment = AudioSegment.from_file(track_path).set_frame_rate(sample_rate).set_channels(1)
                music_segment = sound_slice_normalize(music_segment, sample_rate, (-20, 0))
                track_genre = get_genre(track_path)
                if track_genre != current_genre:
                    current_genre = track_genre
                    log_annotation('music', {'genre': current_genre})
                    print(f"Now playing: {playlist[current_track_index]['name']} - Genre: {current_genre}")


            for i in range(0, len(music_segment), buffer_duration_ms):
                with lock:
                    if not is_playing:
                        break

                music_chunk = music_segment[i:i + buffer_duration_ms]
                adjusted_music_chunk = music_chunk.apply_gain(music_volume)

                voice_chunk = generators.Sine(440).to_audio_segment(duration=len(music_chunk)).set_frame_rate(sample_rate).set_channels(1)
                adjusted_voice_chunk = voice_chunk.apply_gain(mic_volume)

                mixed_chunk = adjusted_music_chunk.overlay(adjusted_voice_chunk)
                mixed_chunk = mixed_chunk.set_channels(channels)
                print(f"Music Segment dBFS: {adjusted_music_chunk.dBFS:.2f}, Voice Segment dBFS: {adjusted_voice_chunk.dBFS:.2f}, Mixed dBFS: {mixed_chunk.dBFS:.2f}")
                mixed_data = mixed_chunk.raw_data
                audio_queue.put(mixed_data)
                print(f"Queued mixed audio chunk.")

                time.sleep(len(mixed_data) / (sample_rate * channels * sample_width))

            with lock:
                current_track_index += 1

        except Exception as e:
            print(f"Error during audio mixing: {str(e)}")
            break

def real_time_playback():
    """Real-time playback using sounddevice."""
    def callback(outdata, frames, time, status):
        """Sounddevice callback function for real-time playback."""
        if status:
            print(status)
        try:
            # Get the next chunk of mixed audio data from the queue
            mixed_data = audio_queue.get_nowait()

            # Convert the mixed audio from bytes to float32 for sounddevice (normalized between -1 and 1)
            audio_data = np.frombuffer(mixed_data, dtype=np.int16).astype(np.float32) / 32768.0

            # Make sure the audio data fits the output format (stereo with `frames` number of samples)
            expected_samples = frames * channels
            if len(audio_data) < expected_samples:
                # Pad with zeros if the data is less than expected
                audio_data = np.pad(audio_data, (0, expected_samples - len(audio_data)), mode='constant')
            elif len(audio_data) > expected_samples:
                # Trim the data if there's more than expected
                audio_data = audio_data[:expected_samples]

            # Reshape the audio data to match stereo format (frames, channels)
            outdata[:] = audio_data.reshape(-1, channels)

        except Empty:
            # If the queue is empty, output silence
            outdata.fill(0)

    # Open sounddevice output stream and start real-time playback
    with sd.OutputStream(samplerate=sample_rate, channels=channels, callback=callback, dtype='float32', blocksize=1024):
        while is_playing or not audio_queue.empty():
            pass  # Keep the stream alive while audio is playing

def write_to_outputs():
    """Continuously write mixed audio data from the queue to the named pipe and a saved WAV file."""
    try:
        with open(output_wav_path, 'wb') as output_pipe, wave.open(saved_wav_path, 'wb') as output_wav:
            output_wav.setnchannels(channels)
            output_wav.setsampwidth(sample_width)
            output_wav.setframerate(sample_rate)

            while is_playing or not audio_queue.empty():
                try:
                    mixed_data = audio_queue.get(timeout=1)
                    output_pipe.write(mixed_data)
                    output_pipe.flush()
                    output_wav.writeframes(mixed_data)
                    print(f"Written chunk to {output_wav_path} and {saved_wav_path}.")
                except Empty:
                    print("Buffer underrun, waiting for data.")
    except Exception as e:
        print(f"Error writing to outputs: {e}")

def start_liquidsoap():
    """Start Liquidsoap for streaming."""
    try:
        subprocess.Popen(["liquidsoap", liq_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        print(f"Error starting Liquidsoap: {e}")

if __name__ == '__main__':
    app.run(debug=True)
