from pydub import AudioSegment, generators

def test_audio_mixing():
    """Test mixing of a sine wave with a music track to ensure correct overlay."""
    try:
        # Generate a sine wave
        voice_segment = generators.Sine(440).to_audio_segment(duration=5000).set_channels(1)
        voice_segment = voice_segment - 90  # Full volume

        # Load a sample music file (replace with any accessible audio file for testing)
        music_segment = AudioSegment.from_file("/home/drakaros/radio_new/playlist/ABTW_Cliff_MAIN.mp3").set_channels(1)
        music_segment = music_segment + 0  # Full volume

        # Ensure both segments have the same duration
        min_length = min(len(music_segment), len(voice_segment))
        music_segment = music_segment[:min_length]
        voice_segment = voice_segment[:min_length]

        # Mix the segments using overlay
        mixed_audio = music_segment.overlay(voice_segment)

        # Export the mixed audio for testing
        mixed_audio.export("test_isolated_mixed.wav", format="wav")
        print("Exported test_isolated_mixed.wav. Check this file to ensure the audio is mixed correctly.")

    except Exception as e:
        print(f"Error during test mixing: {str(e)}")

# Run the test function
test_audio_mixing()
