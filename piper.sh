#!/bin/bash
#
# The use of "Clerk," is a trick to get around the VAD quirk where it ofen does not signal the beginning of speech soon enough.
# the code that reads the transcription will need to filter out these extraneous bits.
# The --sentence-silence value is set to 6 because the recorder code looks for a silence of 5 seconds to signal the
# end of the body.
# The word "Stop." at the end of the text is there to cause the body to have two sentences, otherwise the sound would end after the first one
# with no silence.
echo "Clerk, start a new note. Clerk, This is the title. This is the body, first sentence. Clerk Stop" | uv run piper --model models/en_US-lessac-medium.onnx --sentence-silence 6 --output_file piper_out.wav
aplay piper_out.wav
