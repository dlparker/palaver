import asyncio
import contextvars
import functools
import logging
import traceback
from typing import Optional, Protocol

# Define a ContextVar at the module level (or in a shared utils module).
# This acts as a key for accessing the handler instance.
ERROR_HANDLER = contextvars.ContextVar('ERROR_HANDLER')

class ErrorHandlingException(Exception):

    def __init__(self, *args, **kwargs):
        self.original_exception = kwargs.pop('original_exception')
        super().__init__(*args, **kwargs)
        
class TopLevelCallback(Protocol): # pragma: no cover

    async def on_error(self, error_dict: dict):
        pass
    
class TopLevelCallbackSync(TopLevelCallback):  # pragma: no cover

    def on_error(self, error_dict: dict):
        pass
    
class CleanShutdown:  # pragma: no cover
    
    async def shutdown(self, message: str = None):
        pass
    
class CleanShutdownSync(CleanShutdown):  # pragma: no cover

    def shutdown(self, message: str = None):
        pass

class ForcedShutdown:

    async def shutdown(self, message: str = None):  # pragma: no cover
        pass

class ForcedShutdownSync(ForcedShutdown):

    def shutdown(self, message: str = None):  # pragma: no cover
        pass


class TopErrorHandler:

    def __init__(self,
                 top_level_callback: TopLevelCallback=None,
                 clean_shutdown: CleanShutdown=None,
                 forced_shutdown: ForcedShutdown=None,
                 top_level_callback_sync: TopLevelCallbackSync=None,
                 clean_shutdown_sync: CleanShutdownSync=None,
                 forced_shutdown_sync: ForcedShutdownSync=None,
                 logger = None):
        self.top_level_callback = top_level_callback
        self.clean_shutdown = clean_shutdown
        self.forced_shutdown = forced_shutdown
        self.top_level_callback_sync = top_level_callback_sync
        self.clean_shutdown_sync = clean_shutdown_sync
        self.forced_shutdown_sync = forced_shutdown_sync
        self.logger = logger
        self.async_error_dict = None
        self.sync_error_dict = None
        self.async_error_handled = False
        self.sync_error_handled = False

    async def handle_error(self, task: asyncio.Task, exc: Exception):
        trace_string = traceback.format_exception(exc)
        error_dict = dict(exception=exc, trace_string=trace_string, task=task)
        self.async_error_dict = error_dict
        msg = f"Task {task} raised exception\n{trace_string}"
        if self.logger:
            self.logger.error(msg)
        else:
            print("-------------- TOPLEVEL ERROR DETECTED ---------------------")
            print(msg)
            print("------------------------------------------------------------")
        if self.top_level_callback:
            try:
                await self.top_level_callback.on_error(error_dict)
                self.async_error_handled = True
            except Exception as cbe:
                msg  = f"Toplevel error handler raised exception\n{traceback.format_exc()}"
                if self.logger:
                    self.logger.error(msg)
                else:
                    print("---------- TOPLEVELERROR BAD CONFIGUATION ---------------")
                    print(msg)
        clean_done = False
        if self.clean_shutdown:
            try:
                await self.clean_shutdown.shutdown(f"On error {exc}")
                self.async_error_handled = True
                clean_done = True
            except Exception as cse:
                msg  = f"Clean shutdown raised exception\n{traceback.format_exc()}"
                if self.logger:
                    self.logger.error(msg)
                else:
                    print("---------- TOPLEVELERROR BAD CONFIGUATION ---------------")
                    print(msg)
        if not clean_done and self.forced_shutdown:
            try:
                await self.forced_shutdown.shutdown(f"On error {exc}")
                self.async_error_handled = True
            except Exception as fse:
                msg  = f"Forced shutdown raised exception\n{traceback.format_exc()}"
                if self.logger:
                    self.logger.error(msg)
                else:
                    print("---------- TOPLEVELERROR BAD CONFIGUATION ---------------")
                    print(msg)
                    print("---------------------------------------------------------")

    def post_loop_error(self, error_dict):
        msg = f"Something raised exception\n{error_dict['trace_string']}"
        if self.logger:
            self.logger.error(msg)
        else:
            print("-------------- TOPLEVEL ERROR DETECTED ---------------------")
            print(msg)
            print("------------------------------------------------------------")
        if self.top_level_callback_sync:
            try:
                self.top_level_callback_sync.on_error(error_dict)
            except Exception as cbe:
                msg  = f"Toplevel error handler raised exception\n{traceback.format_exc()}"
                if self.logger:
                    self.logger.error(msg)
                else:
                    print("---------- TOPLEVELERROR BAD CONFIGUATION ---------------")
                    print(msg)
                raise ErrorHandlingException(original_exception=error_dict['exception'])
        clean_done = False
        if self.clean_shutdown_sync:
            try:
                self.clean_shutdown_sync.shutdown(f"On error {error_dict['exception']}")
                clean_done = True
            except Exception as cse:
                msg  = f"Clean shutdown raised exception\n{traceback.format_exc()}"
                if self.logger:
                    self.logger.error(msg)
                else:
                    print("---------- TOPLEVELERROR BAD CONFIGUATION ---------------")
                    print(msg)
                raise ErrorHandlingException(original_exception=error_dict['exception'])

        if not clean_done and self.forced_shutdown_sync:
            try:
                self.forced_shutdown_sync.shutdown(f"On error {error_dict['exception']}")
            except Exception as fse:
                msg  = f"Forced shutdown raised exception\n{traceback.format_exc()}"
                if self.logger:
                    self.logger.error(msg)
                else:
                    print("---------- TOPLEVELERROR BAD CONFIGUATION ---------------")
                    print(msg)
                raise ErrorHandlingException(original_exception=error_dict['exception'])
            
    def run(self, main_coro, *args, **kwargs):
        token = ERROR_HANDLER.set(self)
        
        try:
            res = asyncio.run(main_coro(*args, **kwargs))
            if self.async_error_dict and not self.async_error_handled:
                self.post_loop_error(self.async_error_dict)
        finally:
            # Clean up the context var to avoid leaks in case of reuse.
            ERROR_HANDLER.reset(token)
        return res

    async def async_run(self, main_coro, *args, **kwargs):
        token = ERROR_HANDLER.set(self)
        
        try:
            res = await main_coro(*args, **kwargs)
            if self.async_error_dict and not self.async_error_handled:
                self.post_loop_error(self.async_error_dict)
        finally:
            # Clean up the context var to avoid leaks in case of reuse.
            ERROR_HANDLER.reset(token)
        return res

    def wrap_task(self, coro, *args, **kwargs):
        """
        Optional: A helper to wrap coroutines in tasks with automatic error handling.
        Use this instead of raw asyncio.create_task() for convenience.
        """
        task = asyncio.create_task(coro(*args, **kwargs))
        task.add_done_callback(functools.partial(self._task_done_callback, task))
        return task

    def _task_done_callback(self, task: asyncio.Task, future: asyncio.Future):
        exc = future.exception()
        if exc:
            # Run the handler in the event loop to avoid blocking.
            asyncio.create_task(self.handle_error(task, exc))


# Helper function for any async code to get the handler.
def get_error_handler() -> TopErrorHandler:
    try:
        return ERROR_HANDLER.get()
    except LookupError:
        raise RuntimeError("No TopErrorHandler set in this context. Ensure code runs under TopErrorHandler.run().")

    
