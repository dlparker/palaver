#!/usr/bin/env python
"""
tests/test_vad_recorder_file.py
Test VAD recorder with pre-recorded audio files
"""

import pytest
import asyncio
import logging
from palaver_shared.top_error import (TopErrorHandler,
                                      TopLevelCallback,
                                      CleanShutdown,
                                      ForcedShutdown,
                                      TopLevelCallbackSync,
                                      CleanShutdownSync,
                                      ForcedShutdownSync,
                                      ErrorHandlingException,
                                      get_error_handler,
                                      )
                                     


logger = logging.getLogger("test_code")


def test_lookup_error():
    with pytest.raises(Exception):
        get_error_handler()
        
def test_tlc_1():

    error_dict_1 = None
    class MyTLC(TopLevelCallback):

        async def on_error(self, error_dict: dict):
            nonlocal error_dict_1
            error_dict_1 = error_dict
    

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    tlc = MyTLC()
    tleh = TopErrorHandler(top_level_callback=tlc, logger=logger)
    error_dict_1 = None
    tleh.run(main_loop)
    assert error_dict_1 is not None

    tleh = TopErrorHandler(top_level_callback=tlc, logger=None)
    error_dict_1 = None
    tleh.run(main_loop)
    assert error_dict_1 is not None

def test_tlc_2():

    error_dict_1 = None
    class MyTLC(TopLevelCallback):

        async def on_error(self, error_dict: dict):
            nonlocal error_dict_1
            error_dict_1 = error_dict
    

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        handler = get_error_handler()
        task_1_handle = handler.wrap_task(task_1)
        await asyncio.sleep(0.001)

    tlc = MyTLC()
    tleh = TopErrorHandler(top_level_callback=tlc, logger=logger)
    error_dict_1 = None
    tleh.run(main_loop)
    assert error_dict_1 is not None

    tleh = TopErrorHandler(top_level_callback=tlc, logger=None)
    error_dict_1 = None
    tleh.run(main_loop)
    assert error_dict_1 is not None

def test_csd_1():

    shutdown_flag = None
    class MyCleanDown(CleanShutdown):

        async def shutdown(self, msg):
            nonlocal shutdown_flag
            shutdown_flag = msg

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    mcd = MyCleanDown()
    tleh = TopErrorHandler(clean_shutdown=mcd, logger=logger)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None

    tleh = TopErrorHandler(clean_shutdown=mcd, logger=None)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None
    

def test_fsd_1():

    shutdown_flag = None
    class MyForcedDown(ForcedShutdown):

        async def shutdown(self, msg):
            nonlocal shutdown_flag
            shutdown_flag = msg

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    mfd = MyForcedDown()
    tleh = TopErrorHandler(forced_shutdown=mfd, logger=logger)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None

    tleh = TopErrorHandler(forced_shutdown=mfd, logger=None)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None
    


def test_tlc_sync_1():

    error_dict_1 = None
    class MyTLC(TopLevelCallbackSync):

        def on_error(self, error_dict: dict):
            nonlocal error_dict_1
            error_dict_1 = error_dict

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    tlc = MyTLC()
    tleh = TopErrorHandler(top_level_callback_sync=tlc, logger=logger)
    error_dict_1 = None
    tleh.run(main_loop)
    assert error_dict_1 is not None

    tlc = MyTLC()
    tleh = TopErrorHandler(top_level_callback_sync=tlc, logger=None)
    error_dict_1 = None
    tleh.run(main_loop)
    assert error_dict_1 is not None


def test_csd_sync_1():

    shutdown_flag = None
    class MyCleanDown(CleanShutdownSync):

        def shutdown(self, msg):
            nonlocal shutdown_flag
            shutdown_flag = msg

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    mcd = MyCleanDown()
    tleh = TopErrorHandler(clean_shutdown_sync=mcd, logger=logger)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None

    tleh = TopErrorHandler(clean_shutdown_sync=mcd, logger=None)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None

def test_fsd_sync_1():

    shutdown_flag = None
    class MyForcedDown(ForcedShutdownSync):

        def shutdown(self, msg):
            nonlocal shutdown_flag
            shutdown_flag = msg

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    mfd = MyForcedDown()
    tleh = TopErrorHandler(forced_shutdown_sync=mfd, logger=logger)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None

    tleh = TopErrorHandler(forced_shutdown_sync=mfd, logger=None)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None
    
    
def test_bad_tcl_1():

    error_dict_1 = None
    class MyTLCError(TopLevelCallback):

        async def on_error(self, error_dict: dict):
            raise Exception("error in async on_error")
    
    class MyTLCSync(TopLevelCallbackSync):

        def on_error(self, error_dict: dict):
            nonlocal error_dict_1
            error_dict_1 = error_dict

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    tlc_error = MyTLCError()
    tlc_sync = MyTLCSync()
    tleh = TopErrorHandler(top_level_callback=tlc_error, top_level_callback_sync=tlc_sync, logger=logger)
    error_dict_1 = None
    tleh.run(main_loop)
    assert error_dict_1 is not None

    tleh = TopErrorHandler(top_level_callback=tlc_error, top_level_callback_sync=tlc_sync, logger=None)
    error_dict_1 = None
    tleh.run(main_loop)
    assert error_dict_1 is not None


def test_bad_csd_1():

    shutdown_flag = None
    class MyCleanShutdownError(CleanShutdown):

        def shutdown(self, msg):
            raise Exception("async shutdown error")
            
    class MyCleanDown(CleanShutdownSync):

        def shutdown(self, msg):
            nonlocal shutdown_flag
            shutdown_flag = msg

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    mcd_error = MyCleanShutdownError()
    mcd = MyCleanDown()
    tleh = TopErrorHandler(clean_shutdown=mcd_error, clean_shutdown_sync=mcd, logger=logger)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None

    tleh = TopErrorHandler(clean_shutdown=mcd_error, clean_shutdown_sync=mcd, logger=None)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None

    
def test_bad_fsd_1():

    shutdown_flag = None
    class MyForcedDown(ForcedShutdownSync):

        def shutdown(self, msg):
            nonlocal shutdown_flag
            shutdown_flag = msg

    class MyForcedDownError(ForcedShutdown):

        def shutdown(self, msg):
            raise Exception("error in forced shutdown async")

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    mfd_error = MyForcedDownError()
    mfd = MyForcedDown()
    tleh = TopErrorHandler(forced_shutdown=mfd_error, forced_shutdown_sync=mfd, logger=logger)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None

    tleh = TopErrorHandler(forced_shutdown=mfd_error, forced_shutdown_sync=mfd, logger=None)
    shutdown_flag = None
    tleh.run(main_loop)
    assert shutdown_flag is not None
    
def test_bad_tcl_2():

    error_dict_1 = None
    class MyTLCError(TopLevelCallback):

        async def on_error(self, error_dict: dict):
            raise Exception("error in async on_error")
    
    class MyTLCSyncError(TopLevelCallbackSync):

        def on_error(self, error_dict: dict):
            raise Exception("error in sync on_error")

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    tlc_error = MyTLCError()
    tlc_sync = MyTLCSyncError()
    tleh = TopErrorHandler(top_level_callback=tlc_error, top_level_callback_sync=tlc_sync, logger=logger)
    error_dict_1 = None
    with pytest.raises(ErrorHandlingException):
        tleh.run(main_loop)
    assert error_dict_1 is  None

    tleh = TopErrorHandler(top_level_callback=tlc_error, top_level_callback_sync=tlc_sync, logger=None)
    error_dict_1 = None
    with pytest.raises(ErrorHandlingException):
        tleh.run(main_loop)
    assert error_dict_1 is None

def test_bad_csd_2():

    shutdown_flag = None
    class MyCleanShutdownError(CleanShutdown):

        def shutdown(self, msg):
            raise Exception("async shutdown error")
            
    class MyCleanDownSyncError(CleanShutdownSync):

        def shutdown(self, msg):
            raise Exception("sync shutdown error")

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    mcd_error = MyCleanShutdownError()
    mcd_sync_error = MyCleanDownSyncError()
    tleh = TopErrorHandler(clean_shutdown=mcd_error, clean_shutdown_sync=mcd_sync_error, logger=logger)
    shutdown_flag = None
    with pytest.raises(ErrorHandlingException):
        tleh.run(main_loop)
    assert shutdown_flag is None

    tleh = TopErrorHandler(clean_shutdown=mcd_error, clean_shutdown_sync=mcd_sync_error, logger=None)
    shutdown_flag = None
    with pytest.raises(ErrorHandlingException):
        tleh.run(main_loop)
    assert shutdown_flag is None

    
def test_bad_fsd_2():

    shutdown_flag = None
    class MyForcedDownSyncError(ForcedShutdownSync):

        def shutdown(self, msg):
            raise Exception("error in forced shutdown sync")

    class MyForcedDownError(ForcedShutdown):

        def shutdown(self, msg):
            raise Exception("error in forced shutdown async")

    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    mfd_error = MyForcedDownError()
    mfd_sync = MyForcedDownSyncError()
    tleh = TopErrorHandler(forced_shutdown=mfd_error, forced_shutdown_sync=mfd_sync, logger=logger)
    shutdown_flag = None
    with pytest.raises(ErrorHandlingException):
        tleh.run(main_loop)
    assert shutdown_flag is None

    tleh = TopErrorHandler(forced_shutdown=mfd_error, forced_shutdown_sync=mfd_sync, logger=None)
    shutdown_flag = None
    with pytest.raises(ErrorHandlingException):
        tleh.run(main_loop)
    assert shutdown_flag is None
    

def test_use_most_1():
    
    error_dict_1 = None
    class MyTLC(TopLevelCallback):

        async def on_error(self, error_dict: dict):
            nonlocal error_dict_1
            error_dict_1 = error_dict

    shutdown_flag = None
    class MyCleanDown(CleanShutdown):

        async def shutdown(self, msg):
            nonlocal shutdown_flag
            shutdown_flag = msg

    forced_shutdown_flag = None
    class MyForcedDown(ForcedShutdown):

        async def shutdown(self, msg):
            nonlocal forced_shutdown_flag
            forced_shutdown_flag = msg
            
    async def make_error():
        raise Exception('error_1')

    async def task_1():
        logger.info('in task_1')
        await make_error()
        
    async def main_loop():
        task_1_handle = tleh.wrap_task(task_1)
        await asyncio.sleep(0.001)

    tlc = MyTLC()
    csd = MyCleanDown()
    fsd = MyForcedDown()
    tleh = TopErrorHandler(top_level_callback=tlc, clean_shutdown=csd, forced_shutdown=fsd, logger=logger)

    error_dict_1 = None
    shutdown_flag = None
    forced_shutdown_flag = None

    tleh.run(main_loop)
    
    assert error_dict_1 is not None
    assert shutdown_flag is not None
    assert forced_shutdown_flag is None


    error_dict_1 = None
    shutdown_flag = None
    forced_shutdown_flag = None
    tleh = TopErrorHandler(top_level_callback=tlc, clean_shutdown=None, forced_shutdown=fsd, logger=logger)

    tleh.run(main_loop)
    
    assert error_dict_1 is not None
    assert shutdown_flag is None
    assert forced_shutdown_flag is not None
    
    class MyCleanShutdownError(CleanShutdown):

        def shutdown(self, msg):
            raise Exception("async shutdown error")

    mcd_error = MyCleanShutdownError()
    error_dict_1 = None
    shutdown_flag = None
    forced_shutdown_flag = None
    tleh = TopErrorHandler(top_level_callback=tlc, clean_shutdown=mcd_error, forced_shutdown=fsd, logger=logger)

    tleh.run(main_loop)
    
    assert error_dict_1 is not None
    assert shutdown_flag is None
    assert forced_shutdown_flag is not None

