from pathlib import Path

palaver_root = Path(__file__).parent.parent
sound_root = palaver_root / "sound_files"
signal_sound_files = {
    'new_draft': sound_root / "signal_sounds" / "tos-computer-06.mp3",
    'end_draft': sound_root / "signal_sounds" / "tos-computer-03.mp3",
}
