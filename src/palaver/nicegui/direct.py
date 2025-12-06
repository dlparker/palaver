import asyncio
import json
from nicegui import ui, app
import time
from datetime import datetime
from pathlib import Path

class PalaverApp:
    def __init__(self):
        self.mqtt_client = None
        self.recorder_process = None
        self.session_id = None
        self.start_time = None

        # State tracking
        self.current_mode = "IDLE"
        self.current_vad_silence = "0.8s"
        self.speaking_ticker = ""
        self.queue_count = 0

        # Command state
        self.current_command = None  # Dict: {type, buckets: {name: {display_name, text, status}}}
        self.current_bucket_name = None
        self.completed_notes = []  # List of {id, type, title, timestamp, contents}

        # UI elements (created in build_ui)
        self.label_mode = None
        self.label_speaking = None
        self.label_queue = None
        self.label_elapsed = None
        self.label_vad = None
        self.label_session = None
        self.raw_log = None
        self.current_cmd_card = None
        self.current_cmd_content = None
        self.completed_notes_container = None
        self.btn_start = None
        self.btn_stop = None
        self.recording_indicator = None
        self.process_status = None
        self.debug_panel = None

    async def handle_mqtt_message(self, topic_parts, payload):
        """
        Route MQTT messages to appropriate handlers.

        Args:
            topic_parts: List of topic components (e.g., ['palaver', 'session', '20251205_...', 'vad_mode'])
            payload: JSON payload dict
        """
        try:
            # Extract event type from topic
            # Topic format: palaver/session/{session_id}/{event_type}
            if len(topic_parts) < 4:
                return

            event_type = '/'.join(topic_parts[3:])

            # Route to handler
            if event_type == 'session_started':
                await self.handle_session_started(payload)
            elif event_type == 'recording_state':
                await self.handle_recording_state(payload)
            elif event_type == 'vad_mode':
                await self.handle_vad_mode(payload)
            elif event_type == 'speech_activity':
                await self.handle_speech_activity(payload)
            elif event_type == 'queue_status':
                await self.handle_queue_status(payload)
            elif event_type == 'segment':
                await self.handle_transcription(payload)
            elif event_type == 'command/detected':
                await self.handle_command_detected(payload)
            elif event_type == 'bucket/started':
                await self.handle_bucket_started(payload)
            elif event_type == 'bucket/filled':
                await self.handle_bucket_filled(payload)
            elif event_type == 'command/completed':
                await self.handle_command_completed(payload)
            elif event_type == 'command/aborted':
                await self.handle_command_aborted(payload)

        except Exception as e:
            print(f"Error handling MQTT message: {e}")
            import traceback
            traceback.print_exc()

    async def handle_session_started(self, payload):
        """Handle session_started event"""
        self.session_id = payload['session_id']
        self.start_time = payload['timestamp']

        if self.label_session:
            self.label_session.text = f"SESSION: {self.session_id}"

        print(f"Session started: {self.session_id}")

    async def handle_recording_state(self, payload):
        """Handle recording state changes"""
        is_recording = payload['is_recording']

        if is_recording:
            if self.recording_indicator:
                self.recording_indicator.classes('text-red-500 animate-pulse', remove='text-gray-400')
            if self.process_status:
                self.process_status.text = "Status: Recording"
                self.process_status.classes('text-green-600', remove='text-gray-600')
        else:
            if self.recording_indicator:
                self.recording_indicator.classes('text-gray-400', remove='text-red-500 animate-pulse')
            if self.process_status:
                self.process_status.text = "Status: Stopped"
                self.process_status.classes('text-gray-600', remove='text-green-600')

    async def handle_vad_mode(self, payload):
        """Handle VAD mode changes"""
        self.current_mode = payload['mode'].upper()
        self.current_vad_silence = f"{payload['min_silence_ms'] / 1000:.1f}s"

        if self.label_mode:
            self.label_mode.text = f"MODE: {self.current_mode}"
        if self.label_vad:
            self.label_vad.text = f"VAD: {self.current_vad_silence} silence"

    async def handle_speech_activity(self, payload):
        """Handle speech started/ended events"""
        if payload['started']:
            self.speaking_ticker += "S"
        else:
            self.speaking_ticker += "•"

        # Keep only last 20 characters
        if len(self.speaking_ticker) > 20:
            self.speaking_ticker = self.speaking_ticker[-20:]

        if self.label_speaking:
            self.label_speaking.text = f"SPEAKING: {self.speaking_ticker}"

    async def handle_queue_status(self, payload):
        """Handle transcription queue status updates"""
        self.queue_count = payload['queued_jobs']

        if self.label_queue:
            self.label_queue.text = f"QUEUE: {payload['queued_jobs']} pending"

    async def handle_transcription(self, payload):
        """Handle transcription completion"""
        if not self.raw_log or not self.start_time:
            return

        offset = f"[+{payload['timestamp'] - self.start_time:.3f}]"
        text = payload['text'] if payload['success'] else f"[ERROR]"
        log_line = f"{offset} Seg {payload['segment_index']}: \"{text}\"\n"

        self.raw_log.value += log_line
        # Scroll to bottom
        self.raw_log.run_method('scrollTop', 999999)

    async def handle_command_detected(self, payload):
        """Handle command detection"""
        self.current_command = {
            'type': payload['command_doc_type'],
            'buckets': {},
            'bucket_order': []
        }
        self._update_current_command_ui()

    async def handle_bucket_started(self, payload):
        """Handle bucket started"""
        bucket_name = payload['bucket_name']
        self.current_bucket_name = bucket_name

        if self.current_command:
            self.current_command['buckets'][bucket_name] = {
                'display_name': payload['bucket_display_name'],
                'text': '',
                'status': 'active'
            }
            if bucket_name not in self.current_command['bucket_order']:
                self.current_command['bucket_order'].append(bucket_name)

        self._update_current_command_ui()

    async def handle_bucket_filled(self, payload):
        """Handle bucket filled"""
        bucket_name = payload['bucket_name']

        if self.current_command and bucket_name in self.current_command['buckets']:
            self.current_command['buckets'][bucket_name]['text'] = payload['text']
            self.current_command['buckets'][bucket_name]['status'] = 'filled'

        self._update_current_command_ui()

    async def handle_command_completed(self, payload):
        """Handle command completion"""
        # Extract title for display
        title = "Untitled"
        if 'note_title' in payload['bucket_contents']:
            title = payload['bucket_contents']['note_title'][:50]

        note = {
            'id': len(self.completed_notes) + 1,
            'type': payload['command_type'],
            'title': title,
            'timestamp': datetime.now(),
            'contents': payload['bucket_contents'],
            'files': payload['output_files']
        }
        self.completed_notes.insert(0, note)  # Newest first
        self._add_completed_note_ui(note)

        # Clear current command
        self.current_command = None
        self.current_bucket_name = None
        self._update_current_command_ui()

    async def handle_command_aborted(self, payload):
        """Handle command aborted"""
        self.current_command = None
        self.current_bucket_name = None
        self._update_current_command_ui()

    def _update_current_command_ui(self):
        """Update the current command card"""
        if not self.current_cmd_content:
            return

        self.current_cmd_content.clear()

        if not self.current_command:
            with self.current_cmd_content:
                ui.label("No active command").classes('text-gray-500 italic')
            if self.current_cmd_card:
                self.current_cmd_card.style('display: none')
            return

        if self.current_cmd_card:
            self.current_cmd_card.style('display: block')

        with self.current_cmd_content:
            ui.label(f"COMMAND: {self.current_command['type']}").classes('text-lg font-bold text-orange-900')
            ui.separator()

            # Show buckets in order
            for bucket_name in self.current_command.get('bucket_order', []):
                bucket = self.current_command['buckets'].get(bucket_name, {})
                display_name = bucket.get('display_name', bucket_name)
                text = bucket.get('text', '')
                status = bucket.get('status', 'pending')

                status_icon = "⏳" if status == 'active' else ("✓" if status == 'filled' else "○")

                ui.label(f"{status_icon} {display_name}:").classes('font-bold text-sm mt-2')
                if text:
                    ui.label(text if len(text) < 200 else text[:200] + "...").classes('text-sm ml-4 text-gray-700')
                elif status == 'active':
                    ui.label("[listening...]").classes('text-sm ml-4 text-blue-500 italic')
                else:
                    ui.label("[pending]").classes('text-sm ml-4 text-gray-400 italic')

    def _add_completed_note_ui(self, note):
        """Add a completed note to the UI"""
        if not self.completed_notes_container:
            return

        time_ago = "just now"

        with self.completed_notes_container:
            with ui.expansion(f"Note #{note['id']} – \"{note['title']}\" ({time_ago})",
                            icon='note').classes('w-full bg-white border border-gray-300 mb-2'):
                ui.label(f"Type: {note['type']}").classes('text-sm text-gray-600')
                ui.label(f"Created: {note['timestamp'].strftime('%H:%M:%S')}").classes('text-sm text-gray-600')
                ui.separator()
                for bucket_name, bucket_text in note['contents'].items():
                    ui.label(f"{bucket_name}:").classes('font-bold text-sm mt-2')
                    ui.label(bucket_text).classes('text-sm ml-4 whitespace-pre-wrap')

    async def mqtt_listener(self):
        """Listen for MQTT events and update UI"""
        try:
            async with aiomqtt.Client("localhost") as client:
                self.mqtt_client = client

                # Subscribe to session_started events first
                await client.subscribe("palaver/session/+/session_started")
                print("MQTT: Subscribed to session_started events")

                # Also subscribe to all events (in case session already started)
                await client.subscribe("palaver/session/+/#")
                print("MQTT: Subscribed to all session events")

                async for message in client.messages:
                    # Parse topic
                    topic_parts = message.topic.value.split('/')

                    # Parse JSON payload
                    payload = json.loads(message.payload.decode('utf-8'))

                    # Route to handler
                    await self.handle_mqtt_message(topic_parts, payload)

        except Exception as e:
            print(f"MQTT listener error: {e}")
            import traceback
            traceback.print_exc()
            ui.notify(f"MQTT connection lost: {e}", color='negative')

    async def build_ui(self):
        """Build the UI"""
        # Force light theme
        ui.query('body').style('background: #f8f9fa; color: #1a1a1a')

        # Header
        with ui.header().classes('bg-gradient-to-r from-blue-700 to-blue-500 text-white shadow-lg'):
            ui.label('PALAVER VOICE LOG').classes('text-2xl font-bold tracking-wider')
            ui.space()
            self.recording_indicator = ui.label('●').classes('text-gray-400 text-2xl')

        # Session ID and process status
        with ui.row().classes('px-4 py-2 gap-4'):
            self.label_session = ui.label('SESSION: Waiting...').classes('text-lg')
            self.process_status = ui.label('Status: Idle').classes('text-sm text-gray-600')

        # Status bar
        with ui.row().classes('gap-4 p-4 bg-gray-100 rounded-xl shadow-inner'):
            self.label_mode = ui.label('MODE: IDLE').classes('px-4 py-2 bg-blue-100 rounded-full font-bold')
            self.label_queue = ui.label('QUEUE: 0 pending').classes('px-4 py-2 bg-orange-100 rounded-full')
            self.label_elapsed = ui.label('ELAPSED: 00:00:00').classes('px-4 py-2 bg-teal-100 rounded-full')
            self.label_vad = ui.label('VAD: 0.8s silence').classes('px-4 py-2 bg-purple-100 rounded-full')

        # Speaking ticker
        self.label_speaking = ui.label('SPEAKING: ').classes('text-2xl font-mono tracking-wider text-blue-800 px-4 py-2')

        # Main content area
        with ui.row().classes('gap-6 mt-6 w-full'):
            # Left: Raw transcription
            with ui.column().classes('w-3/5'):
                ui.label('RAW TRANSCRIPTION (Live Log)').classes('text-xl font-bold text-blue-900 mb-2')
                self.raw_log = ui.textarea().props('readonly outlined dense').classes('font-mono text-sm').style('height: 400px; width: 100%')

            # Right: Current command
            with ui.column().classes('w-2/5'):
                ui.label('CURRENT COMMAND').classes('text-xl font-bold text-orange-900 mb-2')
                self.current_cmd_card = ui.card().classes('bg-gradient-to-b from-yellow-50 to-orange-50 border-4 border-orange-400 rounded-xl p-4').style('display: none; min-height: 200px')
                with self.current_cmd_card:
                    self.current_cmd_content = ui.column().classes('w-full')
                    with self.current_cmd_content:
                        ui.label("No active command").classes('text-gray-500 italic')

        # Completed notes
        with ui.column().classes('mt-8 w-full'):
            ui.label('COMPLETED NOTES').classes('text-2xl font-bold text-teal-900 mb-4')
            self.completed_notes_container = ui.column().classes('w-full')

        # Debug panel (collapsible)
        with ui.expansion('Debug Log (Recorder stderr)', icon='terminal').classes('w-full mt-8 bg-gray-200'):
            self.debug_panel = ui.textarea().props('readonly outlined dense').classes('font-mono text-xs').style('height: 300px; width: 100%; background: black; color: lime;')
            self.debug_panel.value = "Waiting for recorder process...\n"

        async def start_mqtt():
            """Delayed startup for MQTT listener"""
            await asyncio.sleep(0.1)  # Let UI initialize
            asyncio.create_task(palaver_app.mqtt_listener())

        ui.timer(0.1, start_mqtt, once=True)

async def setup_palaver():
    # Create and run app
    palaver_app = PalaverApp()
    await palaver_app.build_ui()
    
app.on_startup(setup_palaver)
ui.run(title='Palaver Voice Log', port=8080)
