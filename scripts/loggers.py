import logging
import logging.config

log_format = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'

def setup_logging(default_level="WARNING", info_loggers=None, debug_loggers=None, more_loggers=None):
    config_dict = { 
        'version': 1,
        'disable_existing_loggers': True,
        'formatters': { 
        'standard': { 
            'format': log_format,
        },
        },
        'handlers': { 
            'default': { 
                'level': 'DEBUG',
            'formatter': 'standard',
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',  # Default is stderr
            },
        },
        'loggers': { 
            '': {  # root logger
                'handlers': ['default'],
                'level': default_level,
                'propagate': False
            },
        } 
    }
    if info_loggers is None:
        info_loggers = []
    if debug_loggers is None:
        debug_loggers = []

    def add_one(logger):
        if logger.name in info_loggers:
            level = "INFO"
        elif logger.name in debug_loggers:
            level = "DEBUG"
        else:
            level = default_level
        l_dict =  {
            'handlers': ['default'],
            'level': level,
            'propagate': False
        }
        #print(f'adding logger {logger.name} {l_dict}')
        config_dict['loggers'][logger.name] = l_dict
    if more_loggers:
        for logger in more_loggers:
            add_one(logger)
    for logger in get_loggers():
        add_one(logger)
        
    logging.config.dictConfig(config_dict)


def get_loggers():
    res  = []
    res.append(logging.getLogger("VADFilter"))
    res.append(logging.getLogger("FileListener"))
    res.append(logging.getLogger("AudioMerge"))
    res.append(logging.getLogger("MicListener"))
    res.append(logging.getLogger("BlockAudioRecorder"))
    res.append(logging.getLogger("Commands"))
    res.append(logging.getLogger("DraftMaker"))
    res.append(logging.getLogger("WhisperWrapper"))
    res.append(logging.getLogger("ScribeCore"))
    return res
