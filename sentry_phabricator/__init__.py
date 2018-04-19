"""
sentry_phabricator.plugin
~~~~~~~~~~~~~~~~~~~~~~~~~

:copyright: (c) 2011 by the Sentry Team, see AUTHORS for more details.
:copyright: (c) 2018 by MarcDufresne
:license: BSD, see LICENSE for more details.
"""

try:
    VERSION = __import__('pkg_resources') \
        .get_distribution('sentry-phabricator').version
except Exception, e:
    VERSION = 'unknown'
