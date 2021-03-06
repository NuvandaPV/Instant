
# -*- coding: ascii -*-

"""
A bot library for Instant.
"""

import sys, os, io, re, time, stat
import traceback
import collections, heapq, ast
import json
import socket
import threading

import websocket_server

try:
    from queue import Queue, Empty as QueueEmpty
except ImportError:
    from Queue import Queue, Empty as QueueEmpty

VERSION = 'v1.5.5'

RELAXED_COOKIES = bool(os.environ.get('INSTABOT_RELAXED_COOKIES'))

_unicode = websocket_server.compat.unicode

class EventScheduler(object):
    """
    EventScheduler(time=None, sleep=None) -> new instance

    An EventScheduler executes callbacks at dynamically specified times.

    time is a function that returns the current time as a floating-point
    number; the default implementation returns the current UNIX time. sleep
    is a function that takes a single floating-point argument or None, blocks
    the calling thread for that time (where None means "arbitrarily long"),
    and returns whether there is anything to be done in the EventScheduler's
    queue; the default implementation does this in such a way that it can be
    interrupted by concurrent callback submissions.

    As the requirements suggest, specifying a non-default implementation for
    sleep would be unwise; therefore, time should use the same time units as
    the default sleep, i.e. seconds.

    Callbacks to be run are added to the EventScheduler via add(), add_abs(),
    and add_now(); these return instances of the enclosed Event class that can
    be passed to the cancel() method (of this class) in order to prevent
    them from running (before they have started).

    The actual execution of the callbacks is dispatched by the run() method
    (or its run_once() relative); the method returns whenever there are no
    pending callbacks in the scheduelr and its "forever" flag (which defaults
    to being set; see set_forever()) is cleared. If a thread wants to wait
    for an EventScheduler executing in another thread to finish, it can use
    the join() method for that.

    See also the standard library's "sched" module for similar (but not
    well-suited for concurrent scenarios) functionality.
    """
    class Event:
        """
        Event(time, seq, callback) -> new instance

        An object enclosing a function to be executed by an EventScheduler.

        time is the time at which the callback is to be run; seq is a value
        used to disambiguate events with the same time (e.g. a sequentially
        increasing integer); callback is the actual function to run (taking
        no arguments and with the return value ignored).

        Event objects can be called and forward these calls to their
        callbacks; additionally, comparing two Event objects yields the same
        result as comparing their (time, seq) tuples.
        """
        def __init__(self, time, seq, callback):
            "Instance initializer; see the class docstring for details."
            self.time = time
            self.sortkey = (time, seq)
            self.callback = callback
            self.handled = False
            self.canceled = False
        def __call__(self):
            "Calling protocol support; see the class docstring for details."
            self.callback()
        def __gt__(self, other):
            "Comparison support; see the class docstring for details."
            return self.sortkey > other.sortkey
        def __ge__(self, other):
            "Comparison support; see the class docstring for details."
            return self.sortkey >= other.sortkey
        def __eq__(self, other):
            "Comparison support; see the class docstring for details."
            return self.sortkey == other.sortkey
        def __ne__(self, other):
            "Comparison support; see the class docstring for details."
            return self.sortkey != other.sortkey
        def __le__(self, other):
            "Comparison support; see the class docstring for details."
            return self.sortkey <= other.sortkey
        def __lt__(self, other):
            "Comparison support; see the class docstring for details."
            return self.sortkey < other.sortkey
    def __init__(self, time=None, sleep=None):
        "Instance initializer; see the class docstring for details."
        if time is None: time = self._time
        if sleep is None: sleep = self._sleep
        self.pending = []
        self.time = time
        self.sleep = sleep
        self.forever = True
        self.running = False
        self.cond = threading.Condition()
        self._seq = 0
    def __enter__(self):
        "Context manager entry; internal."
        return self.cond.__enter__()
    def __exit__(self, *args):
        "Context manager exit; internal."
        return self.cond.__exit__(*args)
    def _time(self):
        "Internal: Default implementation of the time callback."
        return time.time()
    def _sleep(self, delay):
        "Internal: Default implementation of the sleep callback."
        with self:
            self.cond.wait(delay)
            return bool(self.pending)
    def add_abs(self, timestamp, callback):
        """
        Schedule callback to be invoked at timestamp and return an Event
        object representing the registration.

        The Event can be passed to cancel() to cancel the callback's execution
        (before it starts). See the class docstring for time unit details.
        """
        with self:
            evt = self.Event(timestamp, self._seq, callback)
            self._seq += 1
            heapq.heappush(self.pending, evt)
            self.cond.notifyAll()
            return evt
    def add(self, delay, callback):
        """
        Schedule callback to be invoked in delay time units and return an
        Event object representing the registration.

        See the notes for add_abs() for more details.
        """
        return self.add_abs(self.time() + delay, callback)
    def add_now(self, callback):
        """
        Schedule callback to be invoked as soon as possible.

        Like add_abs() and add(), this returns an Event object representing
        the registration, but actually cancelling the event may be hard. See
        also the notes for add_abs() for additional details.
        """
        return self.add_abs(self.time(), callback)
    def cancel(self, event):
        """
        Attempt to cancel the given Event's callback's execution and return
        whether that was successful.

        An Event can only be cancelled if its callback has not started
        executing yet.
        """
        with self:
            event.canceled = True
            ret = (not event.handled)
            self.cond.notifyAll()
            return ret
    def clear(self):
        """
        Unconditionally remove all pending callbacks.

        As a side effect and depending on the forever flag, concurrent
        invocation of the run() method may return.
        """
        with self:
            self.pending[:] = []
            self.cond.notifyAll()
    def set_forever(self, v):
        """
        Set whether this EventScheduler should wait for additional tasks when
        there are none queued.

        If this is cleared while there are no queued tasks, the Scheduler may
        shut down as a side effect. If this is cleared while there *are*
        pending tasks, they will be given a chance to execute (and potentially
        to spawn new tasks ad infinitum).
        """
        with self:
            self.forever = v
            self.cond.notifyAll()
    def shutdown(self):
        "A convenience alias for set_forever(False)."
        self.set_forever(False)
    def join(self):
        """
        Wait until a concurrent invocation of the run() method returns.
        """
        with self:
            while self.running:
                self.cond.wait()
    def on_error(self, exc):
        """
        Error handling callback.

        When an exception is raised in a callback, this method is invoked in
        the exception handler with the caught exception object as the only
        argument. sys.exc_info() may be inspected.

        The default implementation re-raises the exception.
        """
        raise
    def run_once(self, hangup=True):
        """
        Execute all currently pending callbacks and wait until it would be
        time to run the next one.

        If hangup is true and there are no callbacks to run, this will wait
        indefinitely.

        Because of the somewhat backwards interface, this method may be of
        little use except as a part of run().
        """
        wait = None
        while 1:
            with self:
                if not self.pending: break
                now = self.time()
                head = self.pending[0]
                if head.time > now and not head.canceled:
                    wait = head.time - now
                    break
                heapq.heappop(self.pending)
                head.handled = True
                if head.canceled: continue
            try:
                head()
            except Exception as exc:
                self.on_error(exc)
        if wait is None and not hangup: return False
        return self.sleep(wait)
    def run(self):
        """
        Execute all currently pending and future callbacks.

        Whenever the forever flag is cleared and there are no pending
        callbacks, this method returns.
        """
        try:
            with self:
                self.running = True
            while 1:
                f = self.forever
                if not self.run_once(f) and not f: break
        finally:
            with self:
                self.running = False
                self.cond.notifyAll()

class AtomicSequence(object):
    """
    AtomicSequence() -> new instance

    An atomic (i.e. thread-safe) counter.

    Instances of this class are callable; they expect no arguments and return
    a single integer which is the current value of the counter. Subsequent
    calls return increasing integers.

    Counting starts at zero: The first call of a new instance will return 0,
    the next will return 1, etc.
    """
    def __init__(self):
        "Instance initializer; see the class docstring for details."
        self.value = -1
        self._lock = threading.Lock()
    def __call__(self):
        "Calling protocol support; see the class docstring for details."
        with self._lock:
            self.value += 1
            return self.value

class InstantClient(object):
    """
    InstantClient(url, [timeout], [cookies], keepalive=False, **kwds)
        -> new instance

    Generic Instant API endpoint wrapper.

    url is the URL of the API endpoint; timeout is the connection timeout (a
    floating-point amount of seconds or None for no timeout), which defaults
    to the TIMEOUT class attribute; cookies is a CookieJar instance (or None)
    which is used for cookie management, and defaults to the COOKIES class
    attribute; keepalive indicates whether the client should reconnect when
    its connection breaks. Unrecognized keyword arguments are ignored.

    timeout and cookies are forwarded to the underlying WebSocket connect()
    call.

    The following groups of methods are provided:
    - The underlying connection can be managed via connect() and close();
      additionally, the keepalive attribute (initialized from the
      corresponding constructor parameter) is relevant.
    - Synchronous sending/receiving can be done via send_*() and recv().
    - Generic connection events as well as certain received data are handled
      by on_*() methods.
    - Specific messages received from the backend are processed by handle_*()
      methods; see in particular on_message() for details on how the default
      implementations dispatch further calls.
    - A main loop managing reconnects, message reception, and event handler
      dispatch is invoked via run() or put into a background thread via
      start().
    """
    TIMEOUT = None
    COOKIES = None
    def __init__(self, url, **kwds):
        "Instance initializer; see the class docstring for details."
        self.url = url
        self.timeout = kwds.get('timeout', self.TIMEOUT)
        self.cookies = kwds.get('cookies', self.COOKIES)
        self.keepalive = kwds.get('keepalive', False)
        self.ws = None
        self.sequence = AtomicSequence()
        self._wslock = threading.RLock()
    def connect(self):
        """
        Create a connection to the stored URL and return it.

        If there already is an active connection, it is returned without
        creating a new one.
        """
        with self._wslock:
            if self.ws is not None: return self.ws
            jar = self.cookies
            self.ws = websocket_server.client.connect(self.url,
                cookies=jar, timeout=self.timeout)
            if isinstance(jar, websocket_server.cookies.FileCookieJar):
                jar.save()
        return self.ws
    def on_open(self):
        """
        Event handler method invoked when the connection opens.

        The default implementation does nothing.
        """
        pass
    def on_message(self, rawmsg):
        """
        Event handler method invoked when a text frame arrives via the
        Websocket.

        rawmsg is the payload of the frame.

        The default implementation decodes the frame as JSON and dispatches
        to one of the handle_*() methods (if the "type" field of the message
        is in a fixed known whitelist) or on_unknown() (otherwise).

        The handle_*() methods and on_unknown() take the same arguments,
        namely the JSON-decoded contents of the frame (typically a dictionary)
        and its raw string form as passed to this method.

        As default, handle_unicast() and handle_broadcast() dispatch the
        "data" field of the received message to on_client_message(); the
        other handle_*() methods do nothing as default (unless, of course,
        they are overridden).
        """
        content = json.loads(rawmsg)
        msgt = content.get('type')
        func = {
            'identity': self.handle_identity, 'pong': self.handle_pong,
            'joined': self.handle_joined, 'who': self.handle_who,
            'unicast': self.handle_unicast,
            'broadcast': self.handle_broadcast,
            'response': self.handle_response, 'left': self.handle_left,
            'error': self.handle_error
        }.get(msgt, self.on_unknown)
        func(content, rawmsg)
    def on_frame(self, msgtype, content, final):
        """
        Event handler method invoked when a binary frame arrives via the
        WebSocket.

        msgtype is the WebSocket opcode of the frame, content is its payload,
        and final indicates whether this is a partial frame (by being False;
        since the default recv() operates in non-streaming mode, final is
        usually always True).

        The default implementation does nothing.
        """
        pass
    def on_connection_error(self, exc):
        """
        Event handler method invoked when connect() raises an exception.

        exc is the exception object. sys.exc_info() may be inspected.

        The default implementation reraises the exception unless the keepalive
        attribute is true.
        """
        if not self.keepalive: raise
    def on_timeout(self, exc):
        """
        Event handler method invoked when the underlying connection times out
        while reading.

        exc is the exception object. sys.exc_info() may be inspected.

        Because the underlying standard library I/O buffers may become
        inconsistent when timeouts happen, the connection is always destroyed
        after this is called. The default implementation re-raises the
        exception unconditionally, effectively handing it off to on_error().
        """
        raise
    def on_error(self, exc):
        """
        Event handler method invoked when a general exception happens during
        the main loop.

        exc is the exception object. sys.exc_info() may be inspected.

        The default implementation re-raises the exception, causing a calling
        run() to abort.
        """
        raise
    def on_close(self, final):
        """
        Event handler method invoked when the underlying connection has
        closed.

        final indicates whether a reconnect is about to happen (False) or
        whether the close is really final (True).

        The default implementation does nothing.
        """
        pass
    def handle_identity(self, content, rawmsg):
        """
        Event handler method for "identity" API messages.

        See on_message() for details.
        """
        pass
    def handle_pong(self, content, rawmsg):
        """
        Event handler method for "pong" API messages.

        See on_message() for details.
        """
        pass
    def handle_joined(self, content, rawmsg):
        """
        Event handler method for "joined" API messages.

        See on_message() for details.
        """
        pass
    def handle_who(self, content, rawmsg):
        """
        Event handler method for "who" API messages.

        See on_message() for details.
        """
        pass
    def handle_unicast(self, content, rawmsg):
        """
        Event handler method for "unicast" API messages.

        See on_message() for details.
        """
        self.on_client_message(content['data'], content, rawmsg)
    def handle_broadcast(self, content, rawmsg):
        """
        Event handler method for "broadcast" API messages.

        See on_message() for details.
        """
        self.on_client_message(content['data'], content, rawmsg)
    def handle_response(self, content, rawmsg):
        """
        Event handler method for "response" API messages.

        See on_message() for details.
        """
        pass
    def handle_left(self, content, rawmsg):
        """
        Event handler method for "left" API messages.

        See on_message() for details.
        """
        pass
    def handle_error(self, content, rawmsg):
        """
        Event handler method for "error" API messages.

        See on_message() for details.
        """
        pass
    def on_unknown(self, content, rawmsg):
        """
        Event handler method for unrecognized API messages.

        See on_message() for details.
        """
        pass
    def on_client_message(self, data, content, rawmsg):
        """
        Event handler method for received inter-client messages.

        data is the client-specified message payload, conventionally a
        dictionary whose "type" entry allows determining its exact purpose;
        content is the backend message enclosing data and contains additional
        metadata (e.g. the sender and the timestamp); rawmsg is the original
        character string as received from the WebSocket.

        The default implementation does nothing.
        """
        pass
    def recv(self):
        """
        Receive and return a single text frame from the underlying WebSocket.

        If there is currently no connection or an EOF is received, this
        returns None. Non-text frames are processed synchronously via
        on_frame(). When a text frame is received, this returns its textual
        content.
        """
        ws = self.ws
        if ws is None: return None
        while 1:
            frame = ws.read_frame()
            if frame is None: return None
            if frame.msgtype != websocket_server.OP_TEXT:
                self.on_frame(frame.msgtype, frame.content, frame.final)
                continue
            return frame.content
    def send_raw(self, rawmsg):
        """
        Send the given text into the underlying WebSocket (nearly) unmodified.

        The input is coerced into a Unicode string; aside from that, no
        transformations are performed. If there is no connection, this raises
        a websocket_server.ConnectionClosedError.

        The default implementation takes only one argument; other ones could
        accept additional (keyword-only) ones.
        """
        ws = self.ws
        if ws is None: raise websocket_server.ConnectionClosedError
        ws.write_text_frame(_unicode(rawmsg))
    def send_seq(self, content, **kwds):
        """
        Augment the given data object with a unique sequence number and send
        its JSON serialization into the underlying WebSocket.

        The sequence number is stored in the "seq" entry of content in-place
        and returned; it is unique from every other sequence number generated
        by this InstantClient instance (via this method).

        Additional keyword-only arguments can be interpreted by overridden
        versions of this method and should be passed on to send_raw() as
        appropriate.
        """
        seq = self.sequence()
        content['seq'] = seq
        self.send_raw(json.dumps(content, separators=(',', ':')), **kwds)
        return seq
    def send_unicast(self, dest, data, **kwds):
        """
        Send a unicast API message to the indicated destination with the given
        data.

        This is a convenience wrapper around send_seq({'type': 'unicast',
        'to': dest, 'data': data}, **kwds).
        """
        return self.send_seq({'type': 'unicast', 'to': dest, 'data': data},
                             **kwds)
    def send_broadcast(self, data, **kwds):
        """
        Send a broadcast API message with the given data.

        This is a convenience wrapper around send_seq({'type': 'broadcast',
        'data': data}, **kwds).
        """
        return self.send_seq({'type': 'broadcast', 'data': data}, **kwds)
    def send_to(self, dest, data, **kwds):
        """
        Send a unicast or broadcast message with the given data.

        If dest is None, the message is a broadcast, otherwise it is a unicast
        directed to dest. Additional keyword-only arguments are forwarded to
        send_broadcast()/send_unicast().
        """
        if dest is None:
            return self.send_broadcast(data, **kwds)
        else:
            return self.send_unicast(dest, data, **kwds)
    def close(self, final=True):
        """
        Close the underlying WebSocket connection.

        If final is true, this clears the keepalive attribute of this instance
        to ensure that its main loop does not attempt to reconnect.
        """
        with self._wslock:
            if self.ws is not None: self.ws.close()
            self.ws = None
            if final: self.keepalive = False
    def run(self):
        """
        The main loop of an InstantClient.

        This takes care of (re)connecting, backing off on failing connection
        attempts, message reading, and connection closing. Most on_*() methods
        are dispatched from here.
        """
        while 1:
            reconnect = 0
            while 1:
                try:
                    self.connect()
                except Exception as exc:
                    self.on_connection_error(exc)
                    time.sleep(reconnect)
                    reconnect += 1
                else:
                    break
            try:
                self.on_open()
                while 1:
                    try:
                        rawmsg = self.recv()
                    except socket.timeout as exc:
                        self.on_timeout(exc)
                        break
                    if rawmsg is None:
                        break
                    elif not rawmsg:
                        continue
                    self.on_message(rawmsg)
            except websocket_server.ConnectionClosedError:
                # Server-side timeouts cause the connection to be dropped.
                pass
            except Exception as exc:
                self.on_error(exc)
            finally:
                final = not self.keepalive
                try:
                    self.close(final)
                except Exception as exc:
                    self.on_error(exc)
                finally:
                    self.on_close(final)
            if final: break
    def start(self):
        """
        Create a daemonic background thread running run() and return it.

        The thread is already started when this returns.
        """
        thr = threading.Thread(target=self.run)
        thr.setDaemon(True)
        thr.start()
        return thr

class Bot(InstantClient):
    """
    Bot(url, nickname=Ellipsis, **kwds) -> new instance

    An InstantClient that maintains a nickname and allows submitting posts.

    url is the WebSocket URL to connect to (typically corresponding to an
    Instant room); nickname is the nickname to use (either a string or None,
    with a None nick rendering the bot invisible (in contrast to an empty
    string as a nick)), defaulting to the NICKNAME class attribute.

    This class can be used as a superclass for complex bots; see also HookBot
    for convenience functionality for simpler ones.
    """
    NICKNAME = None
    def __init__(self, url, nickname=Ellipsis, **kwds):
        "Instance initializer; see the class docstring for details."
        if nickname is Ellipsis: nickname = self.NICKNAME
        InstantClient.__init__(self, url, **kwds)
        self.nickname = nickname
        self.identity = None
        self._nicklock = threading.RLock()
    def on_timeout(self, exc):
        """
        Connection timeout event handler.

        This implementation overrides the behavior from InstantClient by only
        re-raising the exception if there is no connection timeout configured.

        See the base class implementation for more details.
        """
        if self.timeout is None: raise
    def handle_identity(self, content, rawmsg):
        """
        "identity" API message handler.

        This implementation stores the "data" member of the received object
        in the "identity" instance attribute and, taking this to be the start
        of the bot's active lifetime, invokes send_nick() to announce the
        bot's nickname.

        See the base class implementation for more details.
        """
        self.identity = content['data']
        self.send_nick()
    def on_client_message(self, data, content, rawmsg):
        """
        Client-to-client message handler.

        This implementation checks whether the received message is a nickname
        query and responds to it using send_nick() if so.

        See the base class implementation for more details.
        """
        peer = content['from']
        if data.get('type') == 'who' and peer != self.identity['id']:
            self.send_nick(peer)
    def send_nick(self, peer=None):
        """
        Announce this bot's nickname to the given peer or everyone.

        Unless None, peer is the ID of a client to send the announcement to;
        if peer is None, the announcement is broadcast. If this bot's nickname
        is configured to None, no announcement is sent.
        """
        with self._nicklock:
            if self.nickname is None: return
            data = {'type': 'nick', 'nick': self.nickname,
                    'uuid': self.identity['uuid']}
            self.send_to(peer, data)
    def send_post(self, text, parent=None, nickname=Ellipsis):
        """
        Send a chat post.

        text is the content of the post (as a string); parent is the ID of the
        post the new post is a response to (or None to create a new top-level
        post); nickname (if not Ellipsis) allows changing the bot's nickname
        along with the post.
        """
        data = {'type': 'post', 'text': text}
        if parent is not None:
            data['parent'] = parent
        with self._nicklock:
            if nickname is not Ellipsis:
                self.nickname = nickname
            data['nick'] = self.nickname
            return self.send_broadcast(data)

class HookBot(Bot):
    """
    HookBot(url, nickname=Ellipsis, init_cb=None, open_cb=None, post_cb=None,
            close_cb=None) -> new instance

    An extension of Bot that provides externally settable callbacks for key
    events.

    All callbacks take the HookBot instance as the first positional argument
    and are stored in same-named instance attributes; aside from that, their
    signatures and places of invocation differ:
    - init_cb(self) -> None
      Invoked by the HookBot constructor. Allow storing additional state in
      instance attributes.
    - open_cb(self) -> None
      Invoked when a new connection is established. In contrast to init_cb(),
      this may be called multiple times over the lifetime of a HookBot.
    - post_cb(self, post, meta) -> str or None
      Invoked when a new post is submitted. post is the payload of the
      client-to-client message carrying the post, enriched with additional
      information (in-place) by the caller; it has the following entries:
      type     : The string 'post'.
      text     : The textual content of the post.
      nick     : The nickname of the sender (as used for this post).
      timestamp: The timestamp at which the backend processed the post, in
                 milliseconds since the UNIX Epoch.
      id       : A unique ID of the post (as a string).
      from     : The ID of the sender (as a string).
      meta is a dictionary containing additional less-relevant information:
      content: The API message via which the post arrived at the HookBot.
      rawmsg : The textual representation of content as it arrived over the
               wire (aside from UTF-8 decoding).
      reply  : A closure that, when invoked with a single positional argument,
               submits a reply to the post being handled (when it was
               constructed) with the only argument as the reply's text. See
               send_post() for more details.
      The return value of post_cb(), if not None, is sent as a reply to the
      post being handled as if it had been passed to meta['reply'].
    - close_cb(self, final) -> None
      Invoked when a connection is about to be closed. final tells whether
      the close will be followed by a reconnect (final is false) or not (final
      is true).
    """
    def __init__(self, url, nickname=Ellipsis, **kwds):
        "Instance initializer; see the class docstring for details."
        Bot.__init__(self, url, nickname, **kwds)
        self.init_cb = kwds.get('init_cb')
        self.open_cb = kwds.get('open_cb')
        self.post_cb = kwds.get('post_cb')
        self.close_cb = kwds.get('close_cb')
        if self.init_cb is not None: self.init_cb(self)
    def on_open(self):
        """
        Connection opening event handler.

        This implementation invokes the corresponding callback, if any; see
        the class docstring for details.
        """
        Bot.on_open(self)
        if self.open_cb is not None: self.open_cb(self)
    def on_client_message(self, data, content, rawmsg):
        """
        Client-to-client message reception handler.

        This implementation invokes the corresponding callback, if any; see
        the class docstring for details.
        """
        Bot.on_client_message(self, data, content, rawmsg)
        if data.get('type') == 'post' and self.post_cb is not None:
            post = dict(data, timestamp=content['timestamp'],
                id=content['id'], **{'from': content['from']})
            reply = lambda text: self.send_post(text, content['id'])
            res = self.post_cb(self, post, {'content': content,
                                            'rawmsg': rawmsg,
                                            'reply': reply})
            if res is not None: reply(res)
    def on_close(self, final):
        """
        Connection closing event handler.

        This implementation invokes the corresponding callback, if any; see
        the class docstring for details.
        """
        Bot.on_close(self, final)
        if self.close_cb is not None: self.close_cb(self, final)

class Logger:
    """
    Logger(stream) -> new instance

    A Logger writes timestamp-prefixed messages to a stream or swallows them.

    If stream is None, log() writes nothing; otherwise, each logged message
    is prefixed with a timestamp, written to stream, and stream is flushed
    after every message.

    The key method of this class is log(), with its extension log_exception()
    for providing a standartized and compact response to exceptions.

    It is encouraged to write logs in a particular machine-readable fashion,
    which is followed by log_exception(). A machine-readable log line consists
    of the following items:

        [<TIMESTAMP>] <TAG> <key-1>=<value-1> <key-2>=<value-2> ...

    <TIMESTAMP> is provided by log(); <TAG> is a (conventionally uppercase)
    word classifying the log line; any amount of key-value pairs (where keys
    should use lowercase names and separate words using dashes) may follow.
    Values should be alike to Python object literals or bare words (see the
    format() method for details). The module-level read_logs() function can
    read back valid lines in this format.

    An instance of this class pre-configured to write to standard output is
    provided as the module-level DEFAULT_LOGGER variable.
    """
    @classmethod
    def format(cls, obj):
        """
        Pretty-print the given object in a way suitable for inclusion into a
        machine-readable log line.

        Although this method works on any Python object, only the following
        types (some with special formatting as provided by this method) can be
        read back by read_logs():
        - The constants None, True, False, Ellipsis (represented using the
          given names);
        - Decimal integers (matching the regular expression /[+-]?[0-9]+/);
        - Finite floating-point numbers (matching the regular expression
          /[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?/; note that abbreviations
          like 1. or .1 are *not* permitted);
        - Python string literals (an optional "u" prefix is permitted for
          Python 2 compatibility);
        - Tuples or lists of any of the above types (both of these are encoded
          surrounded by parentheses, with the items separated by commata (and
          no spaces), with no trailing comma).
        - Dicts of any of the above types (these are encoded, again, without
          redundant whitespace).
        Note that tuples, lists, and dicts may not be nested (except that
        a tuple/list may be contained immediately inside a dict).

        While format() emits strings in the format described above,
        read_logs() also accepts strings as produced by Python's repr() (which
        include whitespace and potential trailing commata), although the
        format described above is preferred.

        For completeness' sake, in read_logs(), parameter values that do not
        contain any of the forbidden characters matching the character class
        ['"()[\]{},:\s], and are not any of the constant values named above,
        are treated as bare words, i.e. they are decoded to strings without
        further modification. Note that bare words may not be nested inside
        Python-like object literals.
        """
        if isinstance(obj, dict):
            return '{' + ','.join(cls.format(k) + ':' + cls.format(v)
                                  for k, v in obj.items()) + '}'
        elif isinstance(obj, (tuple, list)):
            return '(' + ','.join(map(cls.format, obj)) + ')'
        else:
            return repr(obj)
    def __init__(self, stream):
        "Instance initializer; see the class docstring for details."
        self.stream = stream
    def log(self, msg):
        r"""
        Format a logging line containing the given message and write it to
        the underlying stream.

        msg is the message to be written, and should not be empty (for
        aesthetic reasons); it is advisable to format it in the way presented
        in the class docstring.

        If the underlying stream is None, the formatting and writing is not
        done. Otherwise, after prepending a timestamp, the message is
        formatted into an ASCII-only form (replacing non-ASCII Unicode
        characters with \uXXXX or \UXXXXXXXX escape sequences) the stream is
        always flushed.
        """
        if self.stream is None: return
        m = '[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),
                           msg)
        self.stream.write(m.encode('ascii',
                                   'backslashreplace').decode('ascii'))
        self.stream.flush()
    def log_exception(self, tag, exc, trailer=None):
        """
        Log a compact message informing about the given exception.

        tag is an arbitrary keyword to prepend to the exception information;
        exc is the exception object (for machine readability, it is subjected
        to a double repr() since many exceptions provide custom
        representations that are hard to read back consistently); trailer is
        an optional string to append to the log line.

        The log line is formatted in the way presented in the class docstring:
        After the keyword, the exception's representation is given after a
        "reason=" key; just after the exception, one or two samples from the
        stack trace (as retrieved from sys.exc_info() at hopefully
        informative points) are given as "last-frame=" and "cause-frame="
        (where the latter may be omitted if both frames are the same); after
        all that, trailer is appended after a space (if trailer is not None).
        All of that is then passed on to log().
        """
        try:
            # frame is the frame where the exception is caught; cause is the
            # frame where it originated. The former might be more useful in
            # that it points into the user's code (instead of nested
            # libraries).
            frame = tuple(traceback.extract_tb(sys.exc_info()[2], 1)[-1])
            cause = tuple(traceback.extract_tb(sys.exc_info()[2])[-1])
        except:
            frame, cause = None, None
        # The exception is repr()-ed twice, since many of those objects have
        # custom representations, which are not necessarily machine-readable,
        # and str() is hardly appropriate.
        if frame == cause:
            msg = '%s reason=%r last-frame=%s' % (tag, repr(exc),
                                                  self.format(frame))
        else:
            msg = '%s reason=%r last-frame=%s cause-frame=%s' % (tag,
                repr(exc), self.format(frame), self.format(cause))
        if trailer is not None: msg += ' ' + trailer
        self.log(msg)

DEFAULT_LOGGER = Logger(sys.stdout)

LOGLINE = re.compile(r'^\[([0-9 Z:-]+)\]\s+([A-Z0-9_-]+)(?:\s+(.*))?$')
WHITESPACE = re.compile(r'\s+')
SCALAR = re.compile(r'[^"\'()[\]{},:\s]+|u?"(?:[^"\\]|\\.)*"|'
                    r'u?\'(?:[^\'\\]|\\.)*\'')
COMMA = re.compile(r'\s*,\s*')
TUPLE_ENTRY = re.compile(r'(%s)\s*(,)\s*' % SCALAR.pattern) # for colorlogs.py
TUPLE = re.compile(r'\(\s*(?:(?:%s)%s)*(?:(?:%s)\s*)?\)' %
                   (SCALAR.pattern, COMMA.pattern, SCALAR.pattern))
DICT_ENTRY = re.compile(r'(%s|%s)\s*:\s*(%s|%s)' %
    (SCALAR.pattern, TUPLE.pattern, SCALAR.pattern, TUPLE.pattern))
DICT = re.compile(r'\{\s*(?:%s%s)*(?:%s\s*)?\}' %
                  (DICT_ENTRY.pattern, COMMA.pattern, DICT_ENTRY.pattern))
PARAM = re.compile(r'([a-zA-Z0-9_-]+)=(%s|%s|%s)(?=\s|$)' %
                   (SCALAR.pattern, TUPLE.pattern, DICT.pattern))
INTEGER = re.compile(r'^[+-]?[0-9]+$')
FLOAT = re.compile(r'^[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$')
CONSTANTS = {'None': None, 'True': True, 'False': False,
             'Ellipsis': Ellipsis}
def read_logs(src, filt=None):
    """
    Parse machine-readable logs taken from src.

    src is an iterable producing lines (e.g. a file object); filt is invoked
    on the tag of every line before parsing its key-value section to allow
    quickly rejecting irrelevant lines (a filt of None admits all lines).

    See the Logger class for aid with producing machine-readable logs, as well
    as for documentation on how they are formatted.
    """
    def decode_tuple(val):
        return tuple(ast.literal_eval('[' + val[1:-1] + ']'))
    def decode_dict(val):
        val, idx, ret = val[1:-1], 0, {}
        while 1:
            m = DICT_ENTRY.match(val, idx)
            if not m: break
            idx = m.end()
            rk, rv = m.group(1, 2)
            k = decode_tuple(rk) if rk[0] == '(' else ast.literal_eval(rk)
            v = decode_tuple(rv) if rv[0] == '(' else ast.literal_eval(rv)
            ret[k] = v
            m = COMMA.match(val, idx)
            if not m: break
            idx = m.end()
        if idx != len(val):
            raise RuntimeError('Invalid dictionary literal %r?!' % (val,))
        return ret
    for line in src:
        m = LOGLINE.match(line)
        if not m: continue
        ts, tag, args = m.group(1, 2, 3)
        if filt and not filt(tag): continue
        values = {}
        if args is not None:
            idx = 0
            while idx < len(args):
                m = WHITESPACE.match(args, idx)
                if m:
                    idx = m.end()
                    if idx == len(args): break
                m = PARAM.match(args, idx)
                if not m: break
                idx = m.end()
                name, val = m.group(1, 2)
                if val in CONSTANTS:
                    val = CONSTANTS[val]
                elif INTEGER.match(val):
                    val = int(val)
                elif FLOAT.match(val):
                    val = float(val)
                elif val[0] in '\'"' or val[:2] in ('u"', "u'"):
                    val = ast.literal_eval(val)
                elif val[0] == '(':
                    val = decode_tuple(val)
                elif val[0] == '{':
                    val = decode_dict(val)
                values[name] = val
            if idx != len(args): continue
        yield (ts, tag, values)

class ArgScanner:
    """
    ArgScanner(args, posmin=None, posmax=None) -> new instance

    A simple command-line argument scanner.

    args is a sequence of "raw" arguments (without the program name); posmin
    and posmax indicate, respectively, the minimum and maximum amount of
    positional arguments this scanner will emit.

    This class is intended to be used via the iterator protocol; iterating
    over an instance produces a stream of 2-tuples; in each of the tuples, the
    first entry is one of the strings "opt" or "arg", indicating whether the
    second value is an option or a positional argument. In the latter case,
    the second value is either a single letter (indicating a short option) or
    a long option preceded by a double dash ("--"). After an option has been
    received, the argument() method can be invoked to pull an argument.

    The "raw" arguments are interpreted as follows:
    - A double dash ("--") engages arguments-only mode (without emitting
      anything), where every following raw argument (including another double
      dash) is interpreted as a positional argument.
    - Otherwise, a raw argument starting with a double dash is interpreted as
      a long option. If the raw argument contains an equals sign ("="), the
      name of the option extends up to the equals sign (exclusively), and an
      "option argument" follows after the equals sign, which must be consumed
      via argument() (or ArgScanner raises an error, deeming the command line
      invalid).
    - Otherwise, a raw argument starting with a dash ("-"), but not consisting
      solely of a dash, is interpreted as a group of short options. Each
      character following the dash is emitted as a single short option; if
      argument() is called after a short option has been emitted and there are
      characters in the raw argument after the character being processed,
      those are interpreted as the option argument instead.
    - Otherwise, the raw argument is interpreted as a positional argument.
    - If argument() is called without an option argument as described above
      being available, the next raw argument is consumed and not interpreted
      in any special way. E.g., if the option --foo takes an argument, the
      command line "--foo -- --bar" does *not* result in --bar being
      interpreted as a positional argument.

    If, when all "raw" arguments have been consumed, there have been fewer
    positional arguments processed than posmin (and posmin is not None), an
    error is raised; if posmax arguments have been emitted and another is
    about to be, an error is raised.

    Aside from the iterator protocol and argument(), this class also provides
    some convenience methods, mostly for raising errors.
    """
    OPT = 'opt'
    ARG = 'arg'
    def __init__(self, args, posmin=None, posmax=None):
        "Instance initializer; see the class docstring for details."
        self.args = args
        self.posmin = posmin
        self.posmax = posmax
        self.iter = None
        self.argiter = None
        self.at_arguments = False
        self.last_option = None
        self.next_arg = None
    def __iter__(self):
        "Iterator protocol support; see the class docstring for details."
        return self
    def __next__(self):
        "Iterator protocol support; see the class docstring for details."
        if self.iter is None: self.iter = self._pairs()
        return next(self.iter)
    def next(self):
        "Alias for __next__()."
        if self.iter is None: self.iter = self._pairs()
        return next(self.iter)
    def close(self):
        """
        Stop accepting arguments.

        If there is any pending option or argument, this raises an error;
        otherwise, nothing happens.
        """
        try:
            next(self)
            self.toomany()
        except StopIteration:
            self.iter = None
    def _pairs(self):
        """
        Internal: Generator method backing the iterator protocol support.
        """
        self.argiter = iter(self.args)
        self.at_arguments = False
        self.last_option = None
        self.next_arg = None
        positional = 0
        for arg in self.argiter:
            if self.at_arguments or not arg.startswith('-') or arg == '-':
                positional += 1
                if self.posmax is not None and positional > self.posmax:
                    self.toomany()
                self.last_option = None
                self.next_arg = None
                yield (self.ARG, arg)
            elif arg == '--':
                self.at_arguments = True
            elif not arg.startswith('--'):
                for n, ch in enumerate(arg[1:], 2):
                    self.last_option = '-' + ch
                    if arg[n:]:
                        self.next_arg = arg[n:]
                        yield (self.OPT, ch)
                        if self.next_arg is None: break
                    else:
                        self.next_arg = None
                        yield (self.OPT, ch)
            else:
                idx = arg.find('=')
                if idx == -1:
                    self.last_option = arg
                    self.next_arg = None
                    yield (self.OPT, arg)
                else:
                    self.last_option = arg[:idx]
                    self.next_arg = arg[idx + 1:]
                    yield (self.OPT, self.last_option)
                    if self.next_arg is not None:
                        self._die_opt('Orphaned argument',
                                      tail=': %r' % (self.next_arg,))
        if self.posmin is not None and positional < self.posmin:
            self.toofew()
    def argument(self, type=None):
        """
        Retrieve an argument, optionally converting it to the given type.

        Note that this method is intended to be used while an iteration over
        this ArgScanner is ongoing but currently suspended (e.g. inside a
        "for" loop iterating over this ArgScanner).
        """
        try:
            if self.next_arg is not None:
                arg = self.next_arg
                self.next_arg = None
            else:
                arg = next(self.argiter)
            if type is not None: arg = type(arg)
            return arg
        except StopIteration:
            self._die_opt('Missing required argument')
        except ValueError:
            self._die_opt('Bad argument', tail=': %r' % (arg,))
    def die(self, msg=None):
        """
        Raise a SystemExit exception with the given (optional) message.
        """
        if msg is None: raise SystemExit
        raise SystemExit('ERROR: ' + msg)
    def _die_opt(self, msg, tail=None):
        """
        Internal: Helper method for bailing out of processing an option.

        This constructs a message from msg, the option being processed (if
        any), and tail (if not omitted), and passes that on to die().
        """
        if self.last_option is not None:
            msg += ' for %r' % (self.last_option,)
        if tail is not None:
            msg += tail
        self.die(msg)
    def toomany(self):
        """
        Convenience: Abort with a "Too many arguments" message.
        """
        self.die('Too many arguments')
    def toofew(self):
        """
        Convenience: Abort with a "Too few arguments" message.
        """
        self.die('Too few arguments')
    def unknown(self):
        """
        Convenience: Abort with a message indicating that the current option
        is not recognized.

        If there is no option being processed, this raises a RuntimeError
        instead of the SystemExit from die().
        """
        if self.last_option is None:
            raise RuntimeError('No option to be unknown')
        self.die('Unknown option ' + repr(self.last_option))

def open_file(path, mode, **kwds):
    """
    Helper function for opening files from command-line arguments.

    If path is None or the string "-", a file object wrapping standard input
    or output is opened (depending on the first character of mode), which does
    not close the underlying file descriptor when closed; aside from the
    exception above, all arguments are forwarded to the io.open() function.
    """
    # We use io.open() since it allows using file descriptors in both Py2K
    # and Py3K.
    if path is None or path == '-':
        kwds['closefd'] = False
        if mode[:1] == 'r':
            return io.open(sys.stdin.fileno(), mode, **kwds)
        elif mode[:1] in ('w', 'a'):
            return io.open(sys.stdout.fileno(), mode, **kwds)
        else:
            raise ValueError('Unrecognized open_file() mode: %r' %
                             (mode,))
    else:
        return io.open(path, mode, **kwds)

class OptionParser:
    """
    OptionParser(progname=None) -> new instance

    A declarative command-line argument parser.

    progname is the program name to be used in usage and help displays; if not
    given, "..." is used.

    After an instance is create, options and arguments can be configured via
    the option(), flag(), argument(), etc. methods (see in particular
    help_action() for adding a --help option); after that, parse() can be
    invoked to assimilate arguments, and finally get() to retrieve the
    extracted values.
    """
    Scanner = ArgScanner
    def __init__(self, progname=None):
        "Instance initializer; see the class docstring for details."
        self.progname = progname
        self.description = None
        self.options = collections.OrderedDict()
        self.short_options = collections.OrderedDict()
        self.arguments = []
        self.values = {}
        self.arg_index = 0
    def _add_option(self, opt, kwds):
        "Internal: Commit the given option into internal indexes."
        self.options[opt['option']] = opt
        if opt.get('short'): self.short_options[opt['short']] = opt
    def _set_accum(self, opt, kwds, default=True):
        """
        Internal: Configure the given option's value storing.

        opt is a dictionary describing the option (in an internal format);
        kwds contains keyword arguments given to the function defining the
        option; default tells whether a default value should be transferred
        from kwds to opt.

        The following entries of kwds are interpreted:
        default: If given (and the default argument of this function is true),
                 this configured the default value of the option.
        accum  : If this is a callable, it is an "accumulator" function that
                 is invoked for every appearance of the option on the command
                 line, given two arguments (the current state of the
                 accumulator, initialized as the option's default value (which
                 must be provided in this case, or parsing fails), and the
                 converted value of the latest incarnation of the option), and
                 returns the new value for the option. Otherwise, if this is a
                 truthy value, a default accumulator function is substituted
                 depending on the option's default value: If the default is
                 absent or a Python list, a function that appends to the given
                 list is used; otherwise, a function that invokes the "+="
                 operator on the accumulator is used. Finally, if accum is
                 falsy or absent, no accumulation is done and each appearance
                 of the option overwrites any values stored by previous ones.
        """
        def accum_list(list, item):
            list.append(item)
            return list
        def accum_add(accum, item):
            accum += item
            return accum
        if default and 'default' in kwds:
            opt['default'] = kwds['default']
        if 'accum' not in kwds:
            pass
        elif callable(kwds['accum']):
            opt['accum'] = kwds['accum']
        elif kwds['accum']:
            if 'default' not in opt:
                opt['default'] = []
                opt['accum'] = accum_list
            elif isinstance(opt['default'], list):
                opt['accum'] = accum_list
            else:
                opt['accum'] = accum_add
    def _make_desc(self, opt, name, placeholder):
        """
        Internal: Generate the given option's usage and help information.

        opt is a dictionary describing the option (in an internal format);
        name is the name of the option; placeholder is a placeholder for the
        argument the option takes (if any).
        """
        if name is None:
            if placeholder is None:
                res = '...'
            else:
                res = placeholder
                placeholder = None
        else:
            if opt.get('short'):
                res = '--%s|-%s' % (name, opt['short'])
            else:
                res = '--' + name
        opt['namedesc'] = res
        opt['argdesc'] = placeholder or ''
        if placeholder is not None:
            res += ' ' + placeholder
        if opt.get('accum'):
            res += ' [...]'
        if 'default' in opt or opt.get('omissible'):
            res = '[%s]' % res
        opt['desc'] = res
    def option(self, name, default=None, type=None, **kwds):
        """
        Declare an option taking an argument.

        The following arguments may be passed (see the function signature for
        which are positional and which are keyword-only):
        name       : The name of the option (without the leading "--").
        short      : A single letter (or character) naming a short equivalent
                     for this option.
        help       : A description of the option for the help listing.
        varname    : At which key to store the converted argument; defaults to
                     name.
        default    : The default value for the argument (note that this does
                     *not* undergo conversion via type).
        type       : A callable that takes a string and converts it to some
                     desired type; defaults to str().
        placeholder: A string to represent the argument in usage listings;
                     defaults to the __name__ attribute of type enclosed in
                     less-then/greater-than signs.
        As default subsequent appearances of the option override earlier ones;
        see _set_accum() for means of doing otherwise.
        """
        if type is None: type = str
        try:
            placeholder = kwds['placeholder']
        except KeyError:
            placeholder = '<%s>' % type.__name__
        opt = {'option': name, 'argument': True, 'convert': type,
            'varname': kwds.get('varname', name), 'default': default,
            'help': kwds.get('help'), 'short': kwds.get('short')}
        self._set_accum(opt, kwds)
        self._make_desc(opt, name, placeholder)
        self._add_option(opt, kwds)
    def flag_ex(self, name, value=True, varname=None, **kwds):
        """
        Declare an option taking no argument.

        The following arguments may be passed:
        name   : The name of the flag (without the leading "--").
        short  : A single letter (or character) naming a short equivalent for
                 this flag.
        help   : A description of the flag for the help listing.
        value  : The value to store when the flag is specified.
        varname: At which key to store the value True if the flag is
                 specified; defaults to name.
        default: The default value to use when the flag is not specified. If
                 omitted, nothing is stored when the flag does not appear on
                 the command line; this allows letting a flag set a special
                 value for another option without messing up its default
                 value.
        As default subsequent appearances of the flag override earlier ones;
        see _set_accum() for means of doing otherwise.
        """
        opt = {'option': name, 'varname': varname or name, 'value': value,
            'omissible': True, 'help': kwds.get('help'),
            'short': kwds.get('short')}
        self._set_accum(opt, kwds)
        self._make_desc(opt, name, None)
        self._add_option(opt, kwds)
    def flag(self, name, **kwds):
        """
        Declare an option taking no argument.

        This is a convenience wrapper around flag_ex() that (always) specifies
        default=False; see flag_ex() for details.
        """
        self.flag_ex(name, default=False, **kwds)
    def action(self, name, function, **kwds):
        """
        Declare an action option.

        Differently to the other options, this one executes the given function
        whenever it is encountered on the command line; see help_action()
        for an example. The following arguments can be passed:
        name    : The name of the flag (without the leading "--").
        short   : A single letter (or character) naming a short equivalent for
                  this flag.
        help    : A description of the flag for the help listing.
        function: A callable to execute when the option is encountered. Takes
                  no arguments and the return value is ignored.
        """
        opt = {'option': name, 'omissible': True, 'action': function,
            'help': kwds.get('help'), 'short': kwds.get('short')}
        self._make_desc(opt, name, None)
        self._add_option(opt, kwds)
    def argument(self, name=None, type=None, **kwds):
        """
        Declare a positional argument.

        The following arguments can be passed:
        name       : The name of the argument (used to store values and to
                     construct the default placeholder).
        help       : A description of the argument for the help listing.
        default    : The default value to use if the argument is not
                     specified. If no default is specified and the argument
                     is not passed, the parser aborts.
        type       : A callable that takes a string and converts it to some
                     desired type; defaults to str().
        placeholder: A string to represent the argument in usage listings;
                     defaults to the name parameter of type enclosed in
                     less-then/greater-than signs.
        As default, exactly one value is assigned to the argument; if the
        argument is configured to be accumulating (see _set_accum()), it
        consumes all positional arguments remaining when it is its turn.
        """
        if type is None: type = str
        placeholder = kwds.get('placeholder', '<%s>' % name)
        arg = {'varname': name, 'convert': type, 'help': kwds.get('help')}
        self._set_accum(arg, kwds)
        self._make_desc(arg, None, placeholder)
        self.arguments.append(arg)
    def help_action(self, name='help', help='Display help', desc=Ellipsis):
        """
        Declare a option providing a help listing.

        name is the name of the option; help is the help entry for the help
        option itself; desc, if not omitted, allows defining the "description"
        attribute of the parser, which is printed as part of the help listing.
        """
        if desc is not Ellipsis: self.description = desc
        self.action(name, lambda: self.help(0), help=help)
    def usage(self, exit=None, write=True):
        """
        Format a usage message, optionally print it, and optionally exit.

        If exit is not None, it is a status code to exit with (after writing,
        if enabled); if write is True, the usage message is written to
        standard error. If this method returns, the return value is the
        usage text.
        """
        usage = ' '.join(['USAGE:', self.progname or '...'] +
            [opt['desc'] for opt in self.options.values()] +
            [arg['desc'] for arg in self.arguments])
        if write:
            sys.stderr.write(usage + '\n')
            sys.stderr.flush()
        if exit is not None:
            sys.exit(exit)
        return usage
    def help(self, exit=None, write=True):
        """
        Format the help listing, optionally print it, and optionally exit.

        If exit is not None, it is a status code to exit with (after writing,
        if enabled); if write is True, the help listing is written to standard
        error. If this method returns, the return value is the full text of
        the help listing.
        """
        help = [self.usage(write=False)]
        if self.description is not None: help.append(self.description)
        names, seps, params, helps = [], [], [], []
        for item in list(self.options.values()) + self.arguments:
            if not item['help']: continue
            names.append(item['namedesc'])
            seps.append('' if item.get('option') or not item['argdesc']
                           else ':')
            params.append(item['argdesc'])
            helps.append(item['help'])
        mnl = max(len(n) + len(s) for n, s in zip(names, seps))
        sp = ' ' if any(params) else ''
        mpl = max(map(len, params))
        newline = '\n' + ' ' * (mnl + len(sp) + mpl + 2)
        for n, s, p, h in zip(names, seps, params, helps):
            fn = n.ljust(mnl - len(s)) + s
            help.append('%s%s%-*s: %s' % (fn, sp, mpl, p,
                                          h.replace('\n', newline)))
        help = '\n'.join(help)
        if write:
            sys.stderr.write(help + '\n')
            sys.stderr.flush()
        if exit is not None:
            sys.exit(exit)
        return help
    def parse(self, args=None):
        """
        Parse the given arguments, or the program's command line.

        args is the list of arguments to parse; if it is None, sys.argv[1:]
        is used.

        This initializes this instance's internal storage with the declared
        arguments' defaults (as far as that has not happened yet), and parses
        the given args. The parsing may raise a SystemExit exception to
        indicate parsing errors or general program termination (e.g. after
        printing the help listing).
        """
        def process(opt, value=None):
            if opt.get('action'):
                opt['action']()
                return
            if value is None:
                try:
                    value = opt['value']
                except KeyError:
                    value = parser.argument(opt.get('convert'))
            else:
                value = opt.get('convert', str)(value)
            sv, varname = self.values, opt['varname']
            if 'accum' in opt:
                sv[varname] = opt['accum'](sv[varname], value)
            else:
                sv[varname] = value
        if args is None: args = sys.argv[1:]
        for item in list(self.options.values()) + self.arguments:
            if 'default' not in item: continue
            self.values.setdefault(item['varname'], item['default'])
        parser = self.Scanner(args)
        for tp, arg in parser:
            if tp == 'arg':
                try:
                    desc = self.arguments[self.arg_index]
                    process(desc, arg)
                    if 'accum' not in desc: self.arg_index += 1
                except IndexError:
                    parser.toomany()
            elif arg.startswith('--'):
                try:
                    opt = self.options[arg[2:]]
                except KeyError:
                    parser.unknown()
                process(opt)
            else:
                try:
                    opt = self.short_options[arg]
                except KeyError:
                    parser.unknown()
                process(opt)
        for opt in self.options.values():
            if opt.get('omissible') or opt['varname'] in self.values:
                continue
            parser.die('Missing required option %r' % ('--' + opt['option']))
        for opt in self.arguments:
            if opt.get('omissible') or opt['varname'] in self.values:
                continue
            parser.toofew()
    def get(self, *names, **kwds):
        """
        Retrieve the values of the options with the given names.

        If names has only one entry, the value of the named option is returned
        directly (without being enclosed in a singleton tuple); if the
        keyword-only argument force_tuple is provided and a truthy value, this
        behavior is suppressed.
        """
        force_tuple = kwds.get('force_tuple')
        try:
            if len(names) == 1 and not force_tuple:
                n = names[0]
                return self.values[n]
            ret = []
            # n is referenced by the exception handler.
            for n in names:
                ret.append(self.values[n])
            return ret
        except KeyError:
            if n in self.options: n = '--' + n
            raise SystemExit('ERROR: Missing value for %r' % n)

class CmdlineBotBuilder:
    """
    CmdlineBotBuilder(botcls=None, defnick=None, defurl=Ellipsis)
        -> new instance

    A command-line-based builder for Bot instances.

    botcls is the bot class to instantiate; must be a subclass of Bot (or
    anything that takes parameters compatible to the Bot constructor);
    defaults to HookBot. defnick is the default nickname to use; if None
    is passed (as the default is), the bot stays invisible. defurl is the
    default URL to connect to; if not provided, a URL must be specified
    explicitly; some specialized bots (like Scribe) pass an explicit None
    here, allowing to be run without a URL.

    The intended workflow for this class is like this:
    1. Create an instance, providing suitable default configuration.
    2. Invoke the make_parser() method; use the return value to configure
       bot-specific command-line arguments.
    3. Invoke the parse() method.
    4. Use add() and add_args() to prepare bot constructor parameters.
    5. Invoke this instance to invoke the bot constructor.

    Cookie handling note: This configures the resulting Bot instance(s) for
    cookie management depending on the values passed via the command line.
    If cookie management is enabled, the CookieJar's "relaxed" attribute (for
    relaxing the handling of Secure cookie attribute) is set to the
    relaxed_cookies attribute of the CmdlineBotBuilder instance, which
    defaults to the RELAXED_COOKIES class attribute, which in turn defaults
    (for CmdlineBotBuilder) to the same-named module-level constant (as it was
    when the module was created), whose value is (finally) taken from the
    INSTABOT_RELAXED_COOKIES environment variable (if the variable is
    nonempty, the constant is True, otherwise False).
    """
    Parser = OptionParser
    RELAXED_COOKIES = RELAXED_COOKIES
    def __init__(self, botcls=None, defnick=None, defurl=Ellipsis):
        "Instance initializer; see the class docstring for details."
        if botcls is None: botcls = HookBot
        self.botcls = botcls
        self.defnick = defnick
        self.defurl = defurl
        self.cookies = None
        self.relaxed_cookies = self.RELAXED_COOKIES
        self.args = []
        self.kwds = {}
        self.parser = None
    def make_parser(self, *args, **kwds):
        """
        Create and preliminarily configure the underlying OptionParser.

        All arguments are passed on to the Parser class attribute, which is
        expected to be a subclass of OptionParser (and defaults to
        OptionParser itself), with the exception of the keyword-only desc,
        which is forwarded to the parser's help_action() method instead.
        Returns the parser object for additional configuration.
        """
        desc = kwds.pop('desc', Ellipsis)
        self.parser = self.Parser(*args, **kwds)
        self.parser.help_action(desc=desc)
        self.parser.option('nick', self.defnick,
                           help='Nickname to use')
        self.parser.flag_ex('no-nick', None, 'nick',
                            help='Use no nickname at all')
        self.parser.option('cookies',
                           help='Cookie file (empty string -> memory)')
        self.parser.flag_ex('no-cookies', None, 'cookies',
                            help='Do not save cookies')
        kwargs = {}
        if self.defurl is not Ellipsis: kwargs['default'] = self.defurl
        self.parser.argument('url', help='URL to connect to', **kwargs)
        return self.parser
    def parse(self, argv=None):
        """
        Parse the given arguments.

        If argv is omitted, sys.argv[1:] is used.

        This forwards to the underlying OptionParser's parse() method, and
        configures this instance's state depending on the result.
        """
        self.parser.parse(argv)
        c = self.parser.get('cookies')
        if c is None:
            self.cookies = None
            self.kwds.pop('cookies', None)
        elif not c:
            self.cookies = websocket_server.cookies.CookieJar()
            self.cookies.relaxed = self.relaxed_cookies
            self.kwds['cookies'] = self.cookies
        else:
            self.cookies = websocket_server.cookies.LWPCookieJar(c,
                stat.S_IRUSR | stat.S_IWUSR)
            self.cookies.relaxed = self.relaxed_cookies
            self.cookies.load()
            self.kwds['cookies'] = self.cookies
    def add(self, *args, **kwds):
        """
        Store the given arguments for passing to the Bot constructor.

        If this is called multiple times, new positional arguments come after
        positional arguments specified previously, and new keyword arguments
        override keyword arguments specified previously.

        For more control, you can also mutate the "args" and "kwds" instance
        attributes directly.
        """
        self.args.extend(args)
        self.kwds.update(kwds)
    def add_args(self, *names):
        """
        Add the values of the named options to the pending Bot constructor
        arguments.

        E.g., if add_args('foo') is called, the Bot constructor will receive
        the keyword argument foo= with the value of the option --foo (or the
        argument <foo>).
        """
        for n in names:
            self.kwds[n] = self.parser.get(n)
    def get_args(self, *names, **kwds):
        """
        Retrieve the values of the given options.

        This forwards to the underlying OptionParser's get() method.
        """
        return self.parser.get(*names, **kwds)
    def __call__(self, *args, **kwds):
        """
        Invoke the constructor of the underlying Bot class and return the
        result.

        Any arguments are forwarded to the Bot constructor, as if add() had
        been called with them but the changes by it only applied to this
        call.

        The values of the "url" and "nick" options are always passed as the
        first positional arguments, irrespective of the values of the "args"
        attribute (any values from which come after the two named here).
        """
        a = [self.parser.get('url'), self.parser.get('nick')]
        a.extend(self.args)
        a.extend(args)
        k = dict(self.kwds, **kwds)
        return self.botcls(*a, **k)
