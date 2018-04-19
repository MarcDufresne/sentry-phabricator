"""
sentry_phabricator.plugin
~~~~~~~~~~~~~~~~~~~~~~~~~

:copyright: (c) 2011 by the Sentry Team, see AUTHORS for more details.
:copyright: (c) 2018 by MarcDufresne
:license: BSD, see LICENSE for more details.
"""

from django import forms
from django.utils.translation import ugettext_lazy as _

from sentry.plugins.bases.issue2 import IssuePlugin2

import httplib
import json
import phabricator
import sentry_phabricator
import urlparse


class PhabricatorPlugin(IssuePlugin2):
    author = 'MarcDufresne'
    author_url = 'https://github.com/MarcDufresne/sentry-phabricator'
    version = sentry_phabricator.VERSION
    description = "Integrate Phabricator issue tracking by linking a user account to a project."
    resource_links = [
        ('Bug Tracker', 'https://github.com/MarcDufresne/sentry-phabricator/issues'),
        ('Source', 'https://github.com/MarcDufresne/sentry-phabricator'),
    ]

    slug = 'phabricator'
    title = _('Phabricator')
    conf_title = 'Phabricator'
    conf_key = 'phabricator'

    issue_fields = frozenset(['id', 'url'])

    @staticmethod
    def get_configure_plugin_fields(*args, **kwargs):
        # TODO: How to do validation?
        return [
            {
                'name': 'host',
                'label': 'Phabricator Host (e.g. http://secure.phabricator.org)',
                'type': 'text',
                'help': 'Host of your Phabricator instance, e.g. "http://secure.phabricator.org"'
            },
            {
                'name': 'token',
                'label': 'Conduit API Token',
                'type': 'text',
            },
            {
                'name': 'projectPHIDs',
                'label': 'Project PHIDs (in JSON format)',
                'type': 'textarea',
                'required': False
            }
        ]

    def get_api(self, project):
        api = phabricator.Phabricator(
            host=urlparse.urljoin(self.get_option('host', project), 'api/'),
            token=self.get_option('token', project))
        api.update_interfaces()
        return api

    def needs_auth(self, request, project, **kwargs):
        return False

    def get_auth_for_user(self, user, **kwargs):
        return None

    def is_configured(self, project, **kwargs):
        if not self.get_option('host', project):
            return False
        if self.get_option('token', project):
            return True
        return False

    def _get_priority_choices(self, group):
        try:
            priorities = self.get_api(group.project).maniphest.priority.search()['data']
            choices = []
            default_priority = None
            for p in priorities:
                if 'triage' in p['keywords']:
                    default_priority = 'triage'
                choices.append((p['keywords'][0], p['name']))
            if not default_priority:
                default_priority = choices[0][0]
        except Exception:
            choices = [('triage', "Needs Triage")]
            default_priority = choices[0][0]
        return choices, default_priority

    def _get_status_choices(self, group):
        try:
            statuses = self.get_api(group.project).maniphest.status.search()['data']
            choices = []
            default_status = None
            for s in statuses:
                if s.get('special') == 'default':
                    default_status = s['value']
                choices.append((s['value'], s['name']))
            if not default_status:
                default_status = choices[0][0]
        except Exception:
            choices = [('open', 'Open')]
            default_status = choices[0][0]
        return choices, default_status

    def get_new_issue_fields(self, request, group, event, **kwargs):
        priority_choices, priority_default = self._get_priority_choices(group)
        status_choices, status_default = self._get_status_choices(group)

        return [
            {
                'name': 'title',
                'label': 'Title',
                'default': self.get_group_title(request, group, event),
                'type': 'text'
            },
            {
                'name': 'description',
                'label': 'Description',
                'default': (self.get_group_title(request, group, event) +
                            '\n\n' + self.get_group_description(request, group, event)),
                'type': 'textarea'
            },
            {
                'name': 'priority',
                'label': 'Priority',
                'type': 'select',
                'choices': priority_choices,
                'default': priority_default
            },
            {
                'name': 'status',
                'label': 'Status',
                'type': 'select',
                'choices': status_choices,
                'default': status_default
            },
            {
                'name': 'assigned',
                'label': 'Assign To',
                'type': 'text',
                'required': False,
                'help': 'Name of the user to assign this task to, e.g. "@user" or "user"'
            },
            {
                'name': 'projects',
                'label': 'Additional Projects',
                'type': 'text',
                'required': False,
                'help': 'Comma-separated list of additional projects '
                        'to link to this issue, e.g. "#project1, project2"'
            }
        ]

    @staticmethod
    def _get_user_phid(api, username):
        if username.startswith('@'):
            username = username[1:]
        username.strip()

        try:
            user = api.user.search(constraints={'usernames': [username]})['data'][0]
        except phabricator.APIError, e:
            raise forms.ValidationError('%s %s' % (e.code, e.message))
        except httplib.HTTPException, e:
            raise forms.ValidationError('Unable to reach Phabricator host: %s' % (e.message,))
        return user['phid']

    @staticmethod
    def _get_project_phids(api, projects):
        projects = projects.split(',')
        clean_projects = []
        for project in projects:
            project = project.strip()
            if project.startswith('#'):
                project = project[1:]
            clean_projects.append(project)

        try:
            projects = api.project.search(constraints={'slugs': clean_projects})['data']
        except phabricator.APIError, e:
            raise forms.ValidationError('%s %s' % (e.code, e.message))
        except httplib.HTTPException, e:
            raise forms.ValidationError('Unable to reach Phabricator host: %s' % (e.message,))

        project_phids = []
        for project in projects:
            project_phids.append(project['phid'])
        return project_phids

    @staticmethod
    def _create_transaction(trans_type, trans_value):
        return {
            'type': trans_type,
            'value': trans_value
        }

    def create_issue(self, group, form_data, **kwargs):
        api = self.get_api(group.project)
        try:
            assigned_user_phid = None
            if form_data.get('assigned'):
                assigned_user_phid = self._get_user_phid(api, form_data['assigned'])

            additional_project_phids = []
            if form_data.get('projects'):
                additional_project_phids = self._get_project_phids(api, form_data['projects'])

            project_phids = self.get_option('projectPHIDs', group.project)
            if project_phids:
                project_phids = json.loads(project_phids) + additional_project_phids

            transactions = [
                self._create_transaction('title', form_data['title'].encode('utf-8')),
                self._create_transaction('description', form_data['description'].encode('utf-8')),
                self._create_transaction('projects.set', project_phids),
                self._create_transaction('status', form_data['status']),
                self._create_transaction('priority', form_data['priority']),
                self._create_transaction('owner', assigned_user_phid),
            ]

            data = api.maniphest.edit(transactions=transactions)['object']

        except phabricator.APIError, e:
            raise forms.ValidationError('%s %s' % (e.code, e.message))
        except httplib.HTTPException, e:
            raise forms.ValidationError('Unable to reach Phabricator host: %s' % (e.message,))

        task_id = "{}".format(data['id'])
        return {
            'id': task_id,
            'url': urlparse.urljoin(self.get_option('host', group.project), "T{}".format(task_id))
        }

    def get_link_existing_issue_fields(self, request, group, event, **kwargs):
        return [
            {
                'name': 'task_id',
                'label': 'Task ID',
                'type': 'text',
                'help': 'Enter the Task ID, e.g.: "34" or "T34"'
            },
            {
                'name': 'comment',
                'label': 'Comment',
                'type': 'textarea',
                'required': False,
                'help': 'Leave blank to skip adding a comment on the linked task',
                'default': (self.get_group_title(request, group, event) +
                            '\n\n' + self.get_group_description(request, group, event))
            }
        ]

    def link_issue(self, request, group, form_data, **kwargs):
        api = self.get_api(group.project)

        task_id = form_data['task_id']
        if task_id.startswith('T'):
            task_id = task_id[:1]
        task_id = int(task_id.strip())

        try:
            task = api.maniphest.search(constraints={'ids': [task_id]})['data'][0]
        except phabricator.APIError, e:
            raise forms.ValidationError('%s %s' % (e.code, e.message))
        except httplib.HTTPException, e:
            raise forms.ValidationError('Unable to reach Phabricator host: %s' % (e.message,))
        except Exception, e:
            raise forms.ValidationError('Error while looking for task: %s' % (e.message,))

        if form_data.get('comment'):
            try:
                transactions = [
                    self._create_transaction('comment', form_data['comment'].encode('utf-8'))
                ]
                api.maniphest.edit(transactions=transactions, objectIdentifier=task['id'])
            except Exception:
                pass

        task_id = "{}".format(task['id'])
        return {
            'id': task_id,
            'url': urlparse.urljoin(self.get_option('host', group.project), "T{}".format(task_id))
        }

    def get_issue_url(self, group, issue, **kwargs):
        if isinstance(issue, dict):
            return issue['url']
        return urlparse.urljoin(self.get_option('host', group.project), "T{}".format(issue))

    def get_issue_label(self, group, issue, **kwargs):
        if isinstance(issue, dict):
            return 'T{}'.format(issue['id'])
        return 'T{}'.format(issue)
