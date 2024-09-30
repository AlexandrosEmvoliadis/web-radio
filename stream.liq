# Enable logging
settings.log.stdout.set(true)

# Function to handle the input of the dynamically updated WAV file
def start_wav_stream() =
  # Use input.external.rawaudio as a fallback-safe method to read raw audio data
  source = input.external.rawaudio(
    buffer=2.0,
    max=10.0,
    restart_on_error=true,
    channels=2,
    samplerate=44100,
    "tail -f ./saved_show.wav" # Command to continuously read the updated WAV file
  )

  # Wrap the source in a fail-safe fallback to handle interruptions
  mksafe(source)
end

# Set up the streaming source using the defined function
radio = start_wav_stream()

# Output configuration: Stream the processed audio to Icecast
#output.ao(radio, fallible=true)
output.icecast(
  %mp3(bitrate=128, samplerate=44100, stereo=true),
  host="localhost",
  port=8000,
  password="Mplemare666!",
  mount="stream",
  radio
)
