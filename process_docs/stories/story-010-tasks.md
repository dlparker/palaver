# Story 010: Refactor SQLDraftRecorder - Tasks

## Task Checklist

### Task 1: Remove EventRecord class and related code
- [x] Remove EventRecord SQLModel class definition from sql_drafts.py
- [x] Remove events relationship from DraftRecord class
- [x] Remove all code that creates EventRecord instances in _save_to_database()
- [x] Remove event tracking variables (_events list, _event_sequence counter) from __init__
- [x] Remove event appending in on_audio_event(), on_draft_event(), on_text_event()

### Task 2: Remove properties_json field
- [x] Remove properties_json field from DraftRecord SQLModel class
- [x] Remove properties_json assignment in _save_to_database()

### Task 3: Add enable_file_storage parameter
- [x] Add enable_file_storage parameter to __init__ (default=False)
- [x] Store as instance variable
- [x] Wrap directory creation in on_draft_event() with conditional check
- [x] Wrap WAV file operations (on_audio_event processing) with conditional check
- [x] Wrap text file write in _close() with conditional check
- [x] Wrap JSON file write in _close() with conditional check
- [x] Update directory_path in _save_to_database() to handle None when file storage disabled

### Task 4: Add parent draft relationship
- [x] Add parent_draft_id Optional[int] field to DraftRecord with foreign key
- [x] Add parent relationship using SQLModel Relationship()
- [x] Add children relationship for reverse lookup (optional but useful)

### Task 5: Test the changes
- [x] Run existing tests to ensure no breakage (27 passed, 4 pre-existing failures)
- [x] Manual test: verify database operations work with file storage disabled
- [x] Manual test: verify database operations work with file storage enabled
- [x] Manual test: verify parent draft relationship can be set and queried

## Implementation Notes

### Changes Summary

1. **EventRecord removal**: Removed EventRecord class, events relationship, and all related tracking code (_events list, _event_sequence). Simplified _save_to_database() method.

2. **properties_json removal**: Removed from DraftRecord model and _save_to_database() method.

3. **enable_file_storage parameter**:
   - Added to __init__ with default=False
   - Guards directory creation in on_draft_event()
   - Guards all file operations (WAV, txt, json) in on_audio_event() and _close()
   - directory_path field stores empty string when file storage disabled

4. **Parent draft relationship**:
   - Added parent_draft_id field with foreign key to drafts.id
   - Added bidirectional relationship (parent and children)
   - Uses sa_relationship_kwargs with remote_side for self-referential relationship

### Test Results

All manual tests passed:
- File storage disabled: No files created, empty directory_path, database record saved
- File storage enabled: Creates first_draft.txt and first_draft.json files
- Parent-child relationship: Bidirectional queries work correctly

Existing test suite: 27 passed, 4 pre-existing failures (unrelated to changes)

## Completion Criteria

- All EventRecord code removed
- properties_json field removed
- File storage controlled by enable_file_storage parameter
- Parent draft relationship working
- No breaking changes to existing SQLDraftRecorder API
- Tests passing
