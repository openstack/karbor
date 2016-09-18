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

from datetime import datetime
from datetime import timedelta
import eventlet
import functools
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import timeutils
import six

from karbor import exception
from karbor.i18n import _, _LE
from karbor.services.operationengine.engine import triggers
from karbor.services.operationengine.engine.triggers.timetrigger import\
    time_format_manager

time_trigger_opts = [
    cfg.IntOpt('min_interval',
               default=60 * 60,
               help='The minimum interval of two adjacent time points. '
                    'min_interval >= (max_window_time * 2)'),

    cfg.IntOpt('min_window_time',
               default=900,
               help='The minimum window time'),

    cfg.IntOpt('max_window_time',
               default=1800,
               help='The maximum window time'),
]

CONF = cfg.CONF
CONF.register_opts(time_trigger_opts)
LOG = logging.getLogger(__name__)


class TriggerOperationGreenThread(object):
    def __init__(self, first_run_time, function):

        self._pre_run_time = None
        self._running = False
        self._thread = None

        self._function = function

        self._start(first_run_time)

    def kill(self):
        self._running = False

    @property
    def running(self):
        return self._running

    @property
    def pre_run_time(self):
        return self._pre_run_time

    def _start(self, first_run_time):
        self._running = True

        now = datetime.utcnow()
        initial_delay = 0 if first_run_time <= now else (
            int(timeutils.delta_seconds(now, first_run_time)))

        self._thread = eventlet.spawn_after(
            initial_delay, self._run, first_run_time)
        self._thread.link(self._on_done)

    def _on_done(self, gt, *args, **kwargs):
        self._pre_run_time = None
        self._running = False
        self._thread = None

    def _run(self, expect_run_time):
        while self._running:
            self._pre_run_time = expect_run_time

            expect_run_time = self._function(expect_run_time)
            if expect_run_time is None or not self._running:
                break

            idle_time = int(timeutils.delta_seconds(
                datetime.utcnow(), expect_run_time))
            eventlet.sleep(idle_time)


class TimeTrigger(triggers.BaseTrigger):
    TRIGGER_TYPE = "time"

    TIME_FORMAT_MANAGER = time_format_manager.TimeFormatManager()

    def __init__(self, trigger_id, trigger_property, executor):
        super(TimeTrigger, self).__init__(
            trigger_id, trigger_property, executor)

        self._trigger_property = self.check_trigger_definition(
            trigger_property)

        self._greenthread = None

    def shutdown(self):
        self._kill_greenthread()

    def register_operation(self, operation_id, **kwargs):
        if operation_id in self._operation_ids:
            msg = (_("The operation_id(%s) is exist") % operation_id)
            raise exception.ScheduledOperationExist(msg)

        if self._greenthread and not self._greenthread.running:
            raise exception.TriggerIsInvalid(trigger_id=self._id)

        self._operation_ids.add(operation_id)
        if self._greenthread is None:
            self._start_greenthread()

    def unregister_operation(self, operation_id, **kwargs):
        if operation_id not in self._operation_ids:
            return

        self._operation_ids.remove(operation_id)
        if 0 == len(self._operation_ids):
            self._kill_greenthread()

    def update_trigger_property(self, trigger_property):
        valid_trigger_property = self.check_trigger_definition(
            trigger_property)

        if valid_trigger_property == self._trigger_property:
            return

        first_run_time = self._get_first_run_time(
            valid_trigger_property)
        if not first_run_time:
            msg = (_("The new trigger property is invalid, "
                     "Can not find the first run time"))
            raise exception.InvalidInput(msg)

        if self._greenthread is not None:
            pre_run_time = self._greenthread.pre_run_time
            if pre_run_time:
                end_time = pre_run_time + timedelta(
                    seconds=self._trigger_property['window'])
                if first_run_time <= end_time:
                    msg = (_("The new trigger property is invalid, "
                             "First run time%(t1)s must be after %(t2)s") %
                           {'t1': first_run_time, 't2': end_time})
                    raise exception.InvalidInput(msg)

        self._trigger_property = valid_trigger_property

        if len(self._operation_ids) > 0:
            # Restart greenthread to take the change of trigger property
            # effect immediately
            self._restart_greenthread()

    def _kill_greenthread(self):
        if self._greenthread:
            self._greenthread.kill()
            self._greenthread = None

    def _restart_greenthread(self):
        self._kill_greenthread()
        self._start_greenthread()

    def _start_greenthread(self):
        # Find the first time.
        # We don't known when using this trigger first time.
        first_run_time = self._get_first_run_time(
            self._trigger_property)
        if not first_run_time:
            raise exception.TriggerIsInvalid(trigger_id=self._id)

        self._create_green_thread(first_run_time)

    def _create_green_thread(self, first_run_time):
        func = functools.partial(
            self._trigger_operations,
            trigger_property=self._trigger_property.copy())

        self._greenthread = TriggerOperationGreenThread(
            first_run_time, func)

    def _trigger_operations(self, expect_run_time, trigger_property):
        """Trigger operations once

        returns: wait time for next run
        """

        # Just for robustness, actually expect_run_time always <= now
        # but, if the scheduling of eventlet is not accurate, then we
        # can do some adjustments.
        now = datetime.utcnow()
        if expect_run_time > now and (
                int(timeutils.delta_seconds(now, expect_run_time)) > 0):
            return expect_run_time

        window = trigger_property['window']
        if now > (expect_run_time + timedelta(seconds=window)):
            LOG.exception(_LE("TimeTrigger didn't trigger operation "
                              "on time, now=%(now)s, expect_run_time="
                              "%(expect_run_time)s, window=%(window)d"),
                          {'now': now,
                           'expect_run_time': expect_run_time,
                           'window': window})
        else:
            # The self._executor.execute_operation may have I/O operation.
            # If it is, this green thread will be switched out during looping
            # operation_ids. In order to avoid changing self._operation_ids
            # during the green thread is switched out, copy self._operation_ids
            # as the iterative object.
            operation_ids = self._operation_ids.copy()
            for operation_id in operation_ids:
                try:
                    self._executor.execute_operation(
                        operation_id, now, expect_run_time, window)
                except Exception:
                    LOG.exception(_LE("Submit operation to executor "
                                      "failed, id=%s"),
                                  operation_id)
                    pass

        return self._compute_next_run_time(
            datetime.utcnow(), trigger_property['end_time'],
            trigger_property['format'],
            trigger_property['pattern'])

    @classmethod
    def check_trigger_definition(cls, trigger_definition):
        """Check trigger definition

        All the time instances of trigger_definition are in UTC,
        including start_time, end_time
        """

        trigger_format = trigger_definition.get("format", None)
        pattern = trigger_definition.get("pattern", None)
        cls.TIME_FORMAT_MANAGER.check_time_format(
            trigger_format, pattern)

        start_time = trigger_definition.get("start_time", None)
        if not start_time:
            msg = _("The trigger\'s start time is unknown")
            raise exception.InvalidInput(msg)
        start_time = cls._check_and_get_datetime(start_time, "start_time")

        interval = int(cls.TIME_FORMAT_MANAGER.get_interval(
            trigger_format, pattern))
        if interval is not None and interval < CONF.min_interval:
            msg = (_("The interval of two adjacent time points "
                     "is less than %d") % CONF.min_interval)
            raise exception.InvalidInput(msg)

        window = trigger_definition.get("window", CONF.min_window_time)
        if not isinstance(window, int):
            try:
                window = int(window)
            except Exception:
                msg = (_("The trigger windows(%s) is not integer") % window)
                raise exception.InvalidInput(msg)

        if window < CONF.min_window_time or window > CONF.max_window_time:
            msg = (_("The trigger windows %(window)d must be between "
                     "%(min_window)d and %(max_window)d") %
                   {"window": window,
                    "min_window": CONF.min_window_time,
                    "max_window": CONF.max_window_time})
            raise exception.InvalidInput(msg)

        end_time = trigger_definition.get("end_time", None)
        end_time = cls._check_and_get_datetime(end_time, "end_time")

        valid_trigger_property = trigger_definition.copy()
        valid_trigger_property['window'] = window
        valid_trigger_property['start_time'] = start_time
        valid_trigger_property['end_time'] = end_time
        return valid_trigger_property

    @classmethod
    def _check_and_get_datetime(cls, time, time_name):
        if not time:
            return None

        if isinstance(time, datetime):
            return time

        if not isinstance(time, six.string_types):
            msg = (_("The trigger %(name)s(type = %(vtype)s) is not an "
                     "instance of string") %
                   {"name": time_name, "vtype": type(time)})
            raise exception.InvalidInput(msg)

        try:
            time = timeutils.parse_strtime(time, fmt='%Y-%m-%d %H:%M:%S')
        except Exception:
            msg = (_("The format of trigger %s is not correct") % time_name)
            raise exception.InvalidInput(msg)

        return time

    @classmethod
    def _compute_next_run_time(cls, start_time, end_time,
                               trigger_format, trigger_pattern):

        next_time = cls.TIME_FORMAT_MANAGER.compute_next_time(
            trigger_format, trigger_pattern, start_time)

        if next_time and (not end_time or next_time <= end_time):
            return next_time
        return None

    @classmethod
    def _get_first_run_time(cls, trigger_property):
        now = datetime.utcnow()
        tmp_time = trigger_property['start_time']
        if tmp_time < now:
            tmp_time = now
        return cls._compute_next_run_time(
            tmp_time,
            trigger_property['end_time'],
            trigger_property['format'],
            trigger_property['pattern'])

    @classmethod
    def check_configuration(cls):
        min_window = CONF.min_window_time
        max_window = CONF.max_window_time
        min_interval = CONF.min_interval

        if not (min_window < max_window and (max_window * 2 <= min_interval)):
            msg = (_('Configurations of time trigger are invalid'))
            raise exception.InvalidInput(msg)
