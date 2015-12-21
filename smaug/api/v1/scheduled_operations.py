#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""The ScheduledOperations api."""


from oslo_log import log as logging
import webob
from webob import exc

from smaug.api.openstack import wsgi
from smaug.i18n import _LI
from smaug.operationengine import api as operationengine_api

LOG = logging.getLogger(__name__)


class ScheduledOperationsController(wsgi.Controller):
    """The ScheduledOperations API controller for the OpenStack API."""

    def __init__(self):
        self.operationengine_api = operationengine_api.API()
        super(ScheduledOperationsController, self).__init__()

    def show(self, req, id):
        """Return data about the given scheduled_operation."""
        context = req.environ['smaug.context']
        LOG.info(_LI("Show ScheduledOperation with id: %s"), id,
                 context=context)
        # TODO(chenying)
        return {'Smaug': "ScheduledOperations show."}

    def delete(self, req, id):
        """Delete a scheduled_operation."""
        context = req.environ['smaug.context']

        LOG.info(_LI("Delete ScheduledOperations with id: %s"), id,
                 context=context)

        # TODO(chenying)
        return webob.Response(status_int=202)

    def index(self, req):
        """Returns a summary list of ScheduledOperations."""

        # TODO(chenying)

        return {'scheduled_operation': "ScheduledOperations index."}

    def detail(self, req):
        """Returns a detailed list of ScheduledOperations."""

        # TODO(chenying)

        return {'scheduled_operation': "ScheduledOperations detail."}

    def create(self, req, body):
        """Creates a new ScheduledOperation."""

        LOG.debug('Create ScheduledOperations request body: %s', body)
        context = req.environ['smaug.context']
        request_spec = {'resource': 'ScheduledOperations',
                        'method': 'create'}
        self.operationengine_api.create_scheduled_operation(context,
                                                            request_spec)
        LOG.debug('Create ScheduledOperations request context: %s', context)

        # TODO(chenying)

        return {'scheduled_operation': "Create a ScheduledOperation."}

    def update(self, req, id, body):
        """Update a scheduled_operation."""
        context = req.environ['smaug.context']

        if not body:
            raise exc.HTTPUnprocessableEntity()

        if 'scheduled_operation' not in body:
            raise exc.HTTPUnprocessableEntity()

        scheduled_operation = body['scheduled_operation']

        LOG.info(_LI("Update ScheduledOperation : %s"), scheduled_operation,
                 context=context)

        return {'scheduled_operation': "Update a ScheduledOperation."}


def create_resource():
    return wsgi.Resource(ScheduledOperationsController())
