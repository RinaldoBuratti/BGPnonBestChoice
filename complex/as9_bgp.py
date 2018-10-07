from __future__ import absolute_import
import os

# info on switch
SWITCH_CONTROLLER_INFO = {
    'controller_ip': '90.0.0.2',
    'controller_mac': '02:42:ac:0c:09:00',
    'speaker_ip': '75.0.0.9',
    'speaker_mac': '02:42:ac:0c:09:01',
    'speaker_port': 'eth1'
}
# key: is IP controller (of peering), value: is mac of the interface of OF-switch on peering Lan
INFO_NEIGHBORS = {
    '75.0.0.8': {'mac': '02:42:ac:0f:08:03', 'port': 'eth2', 'as_number': 8},
    '75.0.0.11': {'mac': '02:42:ac:0f:11:02', 'port': 'eth3', 'as_number': 11}
}

NON_BEST_CHOICES = {
    'non_best_choice': False,
    'route': None,
    'id': None,
    'subnet_non_best_traffic': '100.0.0.0/24',
    'ip_non_best_traffic': '100.0.0.0',
    'non_best_origin': '75.0.0.1'
}
# =============================================================================
# BGP configuration.
# =============================================================================
BGP = {
    # AS number for this BGP instance.
    'local_as': 9,

    # BGP Router ID.
    'router_id': '75.0.0.9',

    # List of BGP neighbors.
    # The parameters for each neighbor are the same as the arguments of
    # BGPSpeaker.neighbor_add() method.
    'neighbors': [
        {
            'address': '75.0.0.8',
            'remote_as': 8,
            'connect_mode': 'passive'
        },
        {
            'address': '75.0.0.11',
            'remote_as': 11
        }
    ]
}

# =============================================================================
# Logging configuration.
# =============================================================================
LOGGING = {

    # We use python logging package for logging.
    'version': 1,
    'disable_existing_loggers': False,

    'formatters': {
        'verbose': {
            'format': '%(levelname)s %(asctime)s %(module)s ' +
                      '[%(process)d %(thread)d] %(message)s'
        },
        'simple': {
            'format': '%(levelname)s %(asctime)s %(module)s %(lineno)s ' +
                      '%(message)s'
        },
        'stats': {
            'format': '%(message)s'
        },
    },

    'handlers': {
        # Outputs log to console.
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple'
        },
        'console_stats': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'stats'
        },
        # Rotates log file when its size reaches 10MB.
        'log_file': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join('.', 'bgpspeaker.log'),
            'maxBytes': '10000000',
            'formatter': 'verbose'
        },
        'stats_file': {
            'level': 'DEBUG',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join('.', 'statistics_bgps.log'),
            'maxBytes': '10000000',
            'formatter': 'stats'
        },
    },

    # Fine-grained control of logging per instance.
    'loggers': {
        'bgpspeaker': {
            'handlers': ['console', 'log_file'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'stats': {
            'handlers': ['stats_file', 'console_stats'],
            'level': 'INFO',
            'propagate': False,
            'formatter': 'stats',
        },
    },

    # Root loggers.
    'root': {
        'handlers': ['console', 'log_file'],
        'level': 'DEBUG',
        'propagate': True,
    },
}