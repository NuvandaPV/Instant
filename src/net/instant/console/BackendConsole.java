package net.instant.console;

import java.io.File;
import java.util.List;
import javax.management.AttributeChangeNotification;
import javax.management.JMException;
import javax.management.ListenerNotFoundException;
import javax.management.MBeanNotificationInfo;
import javax.management.MBeanServer;
import javax.management.Notification;
import javax.management.NotificationBroadcasterSupport;
import javax.management.NotificationEmitter;
import javax.management.NotificationFilter;
import javax.management.NotificationListener;
import javax.management.ObjectName;
import javax.script.ScriptException;
import net.instant.console.util.CommandHistory;
import net.instant.console.util.ScriptRunner;
import net.instant.console.util.Util;
import net.instant.console.util.VirtualWriter;
import net.instant.util.argparse.Argument;
import net.instant.util.argparse.ArgumentParser;
import net.instant.util.argparse.Option;
import net.instant.util.argparse.ParseResult;

public class BackendConsole implements BackendConsoleMXBean,
        NotificationEmitter {

    private class EventWriter extends VirtualWriter {

        public long writeSeq(String data) {
            if (data == null) throw new NullPointerException();
            return fireNewOutput(data);
        }

        public void write(String data) {
            writeSeq(data);
        }

        public void write(char[] data, int offset, int length) {
            writeSeq(new String(data, offset, length));
        }

    }

    public static final String OUTPUT_NOTIFICATION = "instant.console.output";

    private static final String MSG_TEXT = "New text appeared on output";
    private static final String MSG_EOF = "End of console output";

    private final BackendConsoleManager parent;
    private final int id;
    private final ScriptRunner runner;
    private final CommandHistory history;
    private final EventWriter writer;
    private final ObjectName objName;
    private final NotificationBroadcasterSupport notifications;
    private MBeanServer server;
    private long notificationSequence;

    protected BackendConsole(BackendConsoleManager parent, int id) {
        this.parent = parent;
        this.id = id;
        this.runner = new ScriptRunner();
        this.history = new CommandHistory();
        this.writer = new EventWriter();
        this.objName = Util.classObjectName(BackendConsole.class,
                                            "name", String.valueOf(id));
        this.notifications = new NotificationBroadcasterSupport(
            new MBeanNotificationInfo(
                new String[] { AttributeChangeNotification.ATTRIBUTE_CHANGE },
                AttributeChangeNotification.class.getName(),
                "An attribute of this MBean has changed"
            ),
            new MBeanNotificationInfo(
                new String[] { OUTPUT_NOTIFICATION },
                String.class.getName(),
                "New text has been output on the console"
            )
        );
        this.server = null;
        this.notificationSequence = 1;
        if (parent != null) {
            runner.setVariable("console", this);
            runner.setVariables(parent.getDefaultVariables());
        }
        runner.redirectOutput(writer);
        history.addListener(new CommandHistory.Listener() {
            public void historyChanged(CommandHistory history) {
                fireHistorySizeChange();
            }
        });
    }
    public BackendConsole() {
        this(null, -1);
    }

    public BackendConsoleManager getParent() {
        return parent;
    }

    public int getID() {
        return id;
    }

    public ScriptRunner getRunner() {
        return runner;
    }

    public CommandHistory getHistory() {
        return history;
    }

    public VirtualWriter getWriter() {
        return writer;
    }

    public ObjectName getObjectName() {
        return objName;
    }

    public MBeanServer getInstalledServer() {
        return server;
    }

    public void install(MBeanServer server) {
        if (server == null) return;
        synchronized (this) {
            if (this.server != null)
                throw new IllegalStateException("Backend console manager " +
                    "is already registered in an MBean server");
            this.server = server;
        }
        try {
            server.registerMBean(this, objName);
        } catch (JMException exc) {
            throw new RuntimeException(exc);
        }
    }

    public int getHistorySize() {
        return history.size();
    }

    public String historyEntry(int index) {
        return history.get(index);
    }

    private String executeCommand(String command) {
        history.add(command);
        Object result = runner.executeSafe(command);
        return (result == null) ? null : result.toString();
    }

    public synchronized String runCommand(String command) {
        String result = executeCommand(command);
        writer.write(ScriptRunner.formatObjectLine(result));
        return (result == null) ? "" : result;
    }

    public synchronized long submitCommand(String command) {
        String result = executeCommand(command);
        result = ScriptRunner.formatObjectLine(result);
        return writer.writeSeq(result);
    }

    public void close() {
        fireNewOutput(null);
        if (parent != null) parent.remove(this);
        MBeanServer server;
        synchronized (this) {
            server = this.server;
            this.server = null;
        }
        if (server != null) {
            try {
                server.unregisterMBean(objName);
            } catch (JMException exc) {
                throw new RuntimeException(exc);
            }
        }
    }

    public MBeanNotificationInfo[] getNotificationInfo() {
        return notifications.getNotificationInfo();
    }

    public void addNotificationListener(NotificationListener listener,
                                        NotificationFilter filter,
                                        Object handback) {
        notifications.addNotificationListener(listener, filter, handback);
    }

    public void removeNotificationListener(NotificationListener listener)
            throws ListenerNotFoundException {
        notifications.removeNotificationListener(listener);
    }

    public void removeNotificationListener(NotificationListener listener,
                                           NotificationFilter filter,
                                           Object handback)
            throws ListenerNotFoundException {
        notifications.removeNotificationListener(listener, filter, handback);
    }

    protected synchronized long fireHistorySizeChange() {
        int historySize = history.size();
        long seq = notificationSequence++;
        Notification n = new AttributeChangeNotification(this,
            seq, System.currentTimeMillis(), "History size changed",
            "HistorySize", "int", historySize - 1, historySize);
        notifications.sendNotification(n);
        return seq;
    }

    protected synchronized long fireNewOutput(String text) {
        long seq = notificationSequence++;
        Notification n = new Notification(OUTPUT_NOTIFICATION, this,
            seq, ((text == null) ? MSG_EOF : MSG_TEXT));
        n.setUserData(text);
        notifications.sendNotification(n);
        return seq;
    }

    public static void main(String[] args) {
        ArgumentParser p = new ArgumentParser("BackendConsole", null,
            "One-shot invocation of the Instant backend console.");
        p.addStandardOptions();
        Option<List<File>> fileOpt = p.add(Option.ofAccum(File.class, "file",
            'f', "A file to execute before the command-line script."));
        Argument<String> scriptArg = p.add(Argument.of(String.class, "script",
            "The script to execute (may be omitted if --file is given)."));
        ParseResult r = p.parseOrExit(args);
        List<File> files = r.get(fileOpt);
        String script = r.get(scriptArg);
        if (files.size() == 0 && script == null) {
            System.err.println(
                "ERROR: Neither a script nor files to execute specified.");
            System.exit(1);
        }
        ScriptRunner runner = new ScriptRunner();
        for (File f : files) {
            try {
                runner.executeFile(f);
            } catch (ScriptException exc) {
                exc.printStackTrace();
                System.exit(2);
            }
        }
        if (script != null) {
            runner.executeAndPrint(script);
        }
    }

}
