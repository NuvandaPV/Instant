
# -*- coding: ascii -*-

# A PEP 342 generator-based coroutine framework.

import sys, os, time
import traceback
import heapq
import signal, select

SIGPIPE_CHUNK_SIZE = 1024
LINEREADER_CHUNK_SIZE = 16384

class ProcessTag(object):
    def __init__(self, pid):
        self.pid = pid

    def __hash__(self):
        return hash(self.pid)

    def __eq__(self, other):
        return isinstance(other, ProcessTag) and other.pid == self.pid

    def __ne__(self, other):
        return not (self == other)

class Suspend(object):
    def apply(self, wake, executor, routine):
        raise NotImplementedError

    def cancel(self):
        raise TypeError('Cannot cancel %s suspend' % self.__class__.__name__)

class CombinationSuspend(Suspend):
    def __init__(self, children):
        self.children = children

    def cancel(self):
        for c in self.children:
            c.cancel()

class All(CombinationSuspend):
    def __init__(self, *suspends):
        CombinationSuspend.__init__(self, suspends)
        self.result = [None] * len(suspends)
        self._finished = [False] * len(suspends)
        self._finishedCount = 0

    def _finish(self, index, value, callback):
        if self._finished[index]:
            raise RuntimeError('Attempting to wake a coroutine more than '
                'once')
        elif self._finishedCount == -1:
            return
        if value is None:
            value = (0, None)
        if value[0] == 1:
            callback(value)
            self._finishedCount = -1
            return
        self.result[index] = value[1]
        self._finished[index] = True
        self._finishedCount += 1
        if self._finishedCount == len(self.children):
            callback((0, self.result))

    def apply(self, wake, executor, routine):
        def make_wake(index):
            return lambda value: self._finish(index, value, wake)
        for n, s in enumerate(self.children):
            s.apply(make_wake(n), executor, routine)

    def cancel(self):
        self._finishedCount = -1
        CombinationSuspend.cancel(self)

class Any(CombinationSuspend):
    def __init__(self, *suspends):
        CombinationSuspend.__init__(self, suspends)
        self._do_wake = True

    def _wake(self, suspend, value, callback):
        if not self._do_wake: return
        self._do_wake = False
        if value is None:
            value = (0, None)
        if value[1] == 1:
            callback(value)
        else:
            callback((0, (suspend, value[1])))
        for s in self.children:
            if s is not suspend:
                s.cancel()

    def apply(self, wake, executor, routine):
        def make_wake(suspend):
            return lambda value: self._wake(suspend, value, wake)
        for s in self.children:
            if not self._do_wake: break
            s.apply(make_wake(s), executor, routine)

    def cancel(self):
        self._do_wake = False
        CombinationSuspend.cancel(self)

class Selector(CombinationSuspend):
    def __init__(self, *suspends):
        CombinationSuspend.__init__(self, list(suspends))
        self._pending = set(suspends)
        self._waiting = set()
        self._finished = {}
        self._callback = None

    def is_empty(self):
        return not (self._pending or self._waiting or self._finished)

    def add(self, suspend):
        self.children.append(suspend)
        self._pending.add(suspend)

    def _wake(self, suspend, value):
        try:
            self._waiting.remove(suspend)
        except KeyError:
            raise RuntimeError('Trying to wake non-suspended coroutine')
        if self._callback is None:
            if suspend in self._pending:
                raise RuntimeError('Trying to wake coroutine more than once')
            self._pending[suspend] = value
        else:
            self._finish((suspend, value))

    def _finish(self, item):
        value = (0, None) if item[1] is None else item[1]
        if value[0] == 1:
            self._callback(value)
        else:
            self._callback((0, item))
        self._callback = None
        self._finished.pop(item[0], None)
        try:
            self.children.remove(item[0])
        except ValueError:
            pass

    def apply(self, wake, executor, routine):
        def make_wake(suspend):
            return lambda value: self._wake(suspend, value)
        self._callback = wake
        if self._pending:
            pending = tuple(self._pending)
            self._pending.clear()
            self._waiting.update(pending)
            for p in pending:
                p.apply(make_wake(p), executor, routine)
        if self._finished:
            self._finish(self._finished.popitem())
        elif self.is_empty() and self._callback:
            wake((None, None))
            self._callback = None

    def cancel(self):
        self._pending.clear()
        self._waiting.clear()
        self._finished.clear()
        self._callback = None
        CombinationSuspend.cancel(self)
        self.children[:] = []

class ControlSuspend(Suspend): pass

class InstantSuspend(ControlSuspend):
    def __init__(self, value=None):
        self.value = value

    def apply(self, wake, executor, routine):
        wake((0, self.value))

class Trigger(InstantSuspend):
    def __init__(self, *triggers, **kwds):
        InstantSuspend.__init__(self, **kwds)
        self.triggers = triggers

    def apply(self, wake, executor, routine):
        for tag, value in self.triggers:
            executor.trigger(tag, (0, value))
        InstantSuspend.apply(self, wake, executor, routine)

class Exit(ControlSuspend):
    def __init__(self, result):
        self.result = result

    def apply(self, wake, executor, routine):
        routine.close()
        executor._done(routine, (0, self.result))

class Join(ControlSuspend):
    def __init__(self, target):
        self.target = target

    def apply(self, wake, executor, routine):
        executor.listen(self.target, wake)

class Spawn(ControlSuspend):
    def __init__(self, target, daemon=False):
        self.target = target
        self.daemon = daemon

    def apply(self, wake, executor, routine):
        executor.add(self.target, daemon=self.daemon)
        wake(None)

class Call(ControlSuspend):
    def __init__(self, target, daemon=False):
        self.target = target
        self.daemon = daemon

    def apply(self, wake, executor, routine):
        executor.add(self.target, daemon=self.daemon)
        executor.listen(self.target, wake)

class Sleep(Suspend):
    def __init__(self, waketime, absolute=False):
        if not absolute: waketime += time.time()
        self.waketime = waketime
        self.cancelled = False

    def __hash__(self):
        return hash(self.waketime)

    def __lt__(self, other):
        return self.waketime <  other.waketime
    def __le__(self, other):
        return self.waketime <= other.waketime
    def __eq__(self, other):
        return self.waketime == other.waketime
    def __ne__(self, other):
        return self.waketime != other.waketime
    def __ge__(self, other):
        return self.waketime >= other.waketime
    def __gt__(self, other):
        return self.waketime >  other.waketime

    def cancel(self):
        self.cancelled = True

    def apply(self, wake, executor, routine):
        def inner_wake(value):
            if self.cancelled: return
            wake(value)
        executor.listen(self, inner_wake)
        executor.add_sleep(self)

class IOSuspend(Suspend):
    SELECT_MODES = {'r': 0, 'w': 1, 'x': 2}

    def __init__(self, file, mode):
        if mode not in self.SELECT_MODES:
            raise ValueError('Invalid I/O mode: %r' % (mode,))
        self.file = file
        self.mode = mode

    def apply(self, wake, executor, routine):
        executor.listen(self.file, wake)
        executor.add_select(self.file, self.SELECT_MODES[self.mode])

class ReadFile(IOSuspend):
    def __init__(self, readfile, length, selectfile=None):
        if selectfile is None: selectfile = readfile
        IOSuspend.__init__(self, selectfile, 'r')
        self.readfile = readfile
        self.length = length
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def apply(self, wake, executor, routine):
        def inner_wake(value):
            if self.cancelled: return
            wake((0, self.readfile.read(self.length)))
        IOSuspend.apply(self, inner_wake, executor, routine)

class WriteFile(IOSuspend):
    def __init__(self, writefile, data, selectfile=None):
        if selectfile is None: selectfile = writefile
        IOSuspend.__init__(self, selectfile, 'w')
        self.writefile = writefile
        self.data = data
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def apply(self, wake, executor, routine):
        def inner_wake(value):
            if self.cancelled: return
            wake((0, self.writefile.write(self.data)))
        IOSuspend.apply(self, inner_wake, executor, routine)

class WaitProcess(Suspend):
    def __init__(self, target):
        self.target = target

    def apply(self, wake, executor, routine):
        if not hasattr(executor, 'waits'):
            raise RuntimeError('Executor not equipped for waiting for '
                'processes')
        if hasattr(self.target, 'poll'):
            executor.waits.add(self.target)
            eff_target = self.target.pid
        else:
            eff_target = self.target
        executor.listen(eff_target, wake)

class Executor:
    def __init__(self):
        self.routines = {}
        self.daemons = set()
        self.suspended = set()
        self.listening = {}
        self.selectfiles = ([], [], [])
        self.sleeps = []
        self.polls = set()

    def add(self, routine, value=None, daemon=False):
        self.routines[routine] = value
        if daemon: self.daemons.add(routine)

    def remove(self, routine):
        self.suspended.discard(routine)
        self.daemons.discard(routine)
        return self.routines.pop(routine, None)

    def _suspend(self, routine):
        if routine not in self.routines:
            raise RuntimeError('Suspending not-running routine')
        del self.routines[routine]
        self.suspended.add(routine)

    def _wake(self, routine, value):
        if routine in self.routines:
            raise RuntimeError('Waking already-woken routine')
        self.suspended.discard(routine)
        self.routines[routine] = value

    def _done(self, routine, result):
        self.remove(routine)
        return self.trigger(routine, result)

    def listen(self, event, callback):
        self.listening.setdefault(event, []).append(callback)

    def trigger(self, event, result=None):
        callbacks = self.listening.pop(event, ())
        for cb in callbacks:
            self._run_callback(cb, (result,), final=True)
        return len(callbacks)

    def add_select(self, file, index):
        l = self.selectfiles[index]
        if file not in l: l.append(file)

    def _done_select(self, readable, writable, exceptable):
        def clear(lst, rem): lst[:] = [f for f in lst if f not in rem]
        if readable: clear(self.selectfiles[0], frozenset(readable))
        if writable: clear(self.selectfiles[1], frozenset(writable))
        if exceptable: clear(self.selectfiles[2], frozenset(exceptable))
        for f in readable: self.trigger(f, (0, 'r'))
        for f in writable: self.trigger(f, (0, 'w'))
        for f in exceptable: self.trigger(f, (0, 'x'))

    def add_sleep(self, sleep):
        heapq.heappush(self.sleeps, sleep)

    def _finish_sleeps(self, now=None):
        if now is None: now = time.time()
        while self.sleeps and self.sleeps[0].waketime <= now:
            sleep = heapq.heappop(self.sleeps)
            self.trigger(sleep)

    def add_poll(self, poll):
        self.polls.add(poll)

    def remove_poll(self, poll):
        self.polls.discard(poll)

    def _do_polls(self):
        for p in tuple(self.polls):
            res = self._run_callback(p, (self,), final=True)
            if res[0] == 0 and res[1] or res[1] == 1:
                self.remove_poll(p)

    def on_error(self, exc, source):
        lines = ['Uncaught exception in %r:\n' % (source,)]
        lines.extend(traceback.format_exception(*sys.exc_info()))
        sys.stderr.write(''.join(lines))

    def _run_callback(self, func, args, kwds=None, final=False):
        if kwds is None: kwds = {}
        try:
            return (0, func(*args, **kwds))
        except Exception as exc:
            if final: self.on_error(exc, func)
            return (1, exc)

    def close(self):
        for r in self.routines:
            r.close()
        for r in self.suspended:
            r.close()
        self.routines = {}
        self.suspended = set()
        self.listening = {}
        self.sleeps = []
        self.selectfiles = ([], [], [])

    def run(self):
        def make_wake(routine):
            return lambda value: self._wake(routine, value)
        while self.routines or self.suspended:
            runqueue = tuple(self.routines.items())
            if all(e[0] in self.daemons for e in runqueue):
                break
            for r, v in runqueue:
                self.routines[r] = None
                if v is None: v = (0, None)
                # Here, we cannot use _run_callback() since we need after the
                # fact whether to run on_error() or not; as a bonus, we get
                # a more precise error source.
                try:
                    resume = (r.send, r.throw)[v[0]]
                    suspend = resume(v[1])
                except StopIteration:
                    self._done(r, None)
                    continue
                except Exception as exc:
                    if not self._done(r, (1, exc)):
                        self.on_error(exc, r)
                    continue
                self._suspend(r)
                res = self._run_callback(suspend.apply, (make_wake(r), self,
                                                         r))
                if res[0] == 1:
                    self._wake(r, res)
            if any(self.selectfiles):
                if self.routines or not self.sleeps:
                    timeout = 0
                else:
                    timeout = self.sleeps[0].waketime - time.time()
                result = select.select(self.selectfiles[0],
                                       self.selectfiles[1],
                                       self.selectfiles[2],
                                       timeout)
                self._done_select(*result)
            elif self.sleeps and not self.routines:
                time.sleep(self.sleeps[0].waketime - time.time())
            self._finish_sleeps()
            self._do_polls()

    def __call__(self):
        self.run()

class BinaryLineReader(object):
    class _Suspend(Suspend):
        def __init__(self, parent):
            self.parent = parent
            self._delegate = None

        def apply(self, wake, executor, routine):
            def inner_wake(result):
                if result[0] == 1:
                    wake(result)
                    return
                elif result[1]:
                    self.parent._buffer += result[1]
                else:
                    self.parent._eof = True
                self.apply(wake, executor, routine)
            line = self.parent._extract_line()
            if line is None:
                self._delegate = ReadFile(self.parent.file,
                                          LINEREADER_CHUNK_SIZE)
                self._delegate.apply(inner_wake, executor, routine)
                return
            wake((0, line))

        def cancel(self):
            if self._delegate is not None:
                self._delegate.cancel()

    def __init__(self, file, encoding=None, errors=None):
        self.file = file
        self.encoding = encoding
        self.errors = errors
        self.chunk_size = LINEREADER_CHUNK_SIZE
        self._buffer = b''
        self._eof = False

    def _extract_line(self):
        line, sep, rest = self._buffer.partition(b'\n')
        if not sep and not self._eof:
            return None
        self._buffer = rest
        line += sep
        if self.encoding is not None:
            line = line.decode(self.encoding, self.errors)
        return line

    def ReadLine(self):
        return self._Suspend(self)

def sigpipe_handler(rfp, wfp, waits):
    try:
        while 1:
            yield ReadFile(rfp, SIGPIPE_CHUNK_SIZE)
            wakelist = []
            for w in tuple(waits):
                res = w.poll()
                if res is not None:
                    waits.remove(w)
                    wakelist.append((ProcessTag(w.pid), res))
            while 1:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid == 0: break
                code = -(status & 0x7F) if status & 0xFF else status >> 8
                wakelist.append((ProcessTag(pid), code))
            yield Trigger(*wakelist)
    finally:
        signal.set_wakeup_fd(-1)
        for fp in (rfp, wfp):
            try:
                fp.close()
            except IOError:
                pass

def set_sigpipe(executor, coroutine=sigpipe_handler):
    rfd, wfd = os.pipe()
    rfp, wfp = os.fdopen(rfd, 'rb', 0), os.fdopen(wfd, 'wb', 0)
    waits = set()
    try:
        signal.set_wakeup_fd(wfd)
        inst = coroutine(rfp, wfp, waits)
        executor.add(inst, daemon=True)
        executor.waits = waits
    except Exception:
        for fd in (rfd, wfd):
            try:
                os.close(fd)
            except IOError:
                pass
        raise
    finally:
        try:
            del executor.waits
        except AttributeError:
            pass
    return inst

def run(routines=(), main=None, sigpipe=False):
    def main_routine():
        result[0] = yield main
    ex = Executor()
    for r in routines: ex.add(r)
    if main: ex.add(main_routine())
    if sigpipe: set_sigpipe(ex)
    result = [None]
    try:
        ex.run()
    finally:
        ex.close()
    return result[0]
