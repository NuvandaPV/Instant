package net.instant;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.PrintStream;
import java.net.URL;
import java.util.jar.Manifest;
import java.util.logging.Level;
import java.util.logging.Logger;
import java.util.regex.Pattern;
import net.instant.hooks.APIWebSocketHook;
import net.instant.util.Formats;
import net.instant.util.Logging;
import net.instant.util.argparse.ArgumentParser;
import net.instant.util.argparse.ParseException;
import net.instant.util.argparse.ParseResult;
import net.instant.util.argparse.ValueArgument;
import net.instant.util.argparse.ValueOption;
import net.instant.util.fileprod.FSResourceProducer;
import net.instant.ws.InstantWebSocketServer;

public class Main implements Runnable {

    public static final String APPNAME = "Instant";
    public static final String VERSION = "1.4.3";
    public static final String FINE_VERSION;

    public static final String ROOM_RE =
        "[a-zA-Z](?:[a-zA-Z0-9_-]*[a-zA-Z0-9])?";
    public static final String STAGING_RE = "dev/[a-zA-Z0-9-]+";

    private static final String VERSION_FILE;
    private static final Logger LOGGER;

    static {
        Logging.initFormat();
        LOGGER = Logger.getLogger("Main");
        String v;
        InputStream stream = null;
        try {
            stream = new URL(Main.class.getResource(""),
                             "/META-INF/MANIFEST.MF").openStream();
            Manifest mf = new Manifest(stream);
            v = mf.getMainAttributes().getValue("X-Git-Commit");
        } catch (IOException exc) {
            v = null;
        } finally {
            try {
                if (stream != null) stream.close();
            } catch (IOException exc) {}
        }
        FINE_VERSION = v;
        VERSION_FILE = String.format("this._instantVersion_ = " +
            "{version: %s, revision: %s};\n",
            Formats.escapeJSString(VERSION, true),
            Formats.escapeJSString(FINE_VERSION, true));
    }

    private final String[] args;
    private InstantRunner runner;
    private String startupCmd;

    public Main(String[] args) {
        this.args = args;
        runner = new InstantRunner();
    }

    public int getArgumentCount() {
        return args.length;
    }

    public String getArgument(int i) {
        if (i < 0 || i > args.length)
            throw new IndexOutOfBoundsException("Invalid index " + i);
        return args[i];
    }

    public InstantRunner getRunner() {
        return runner;
    }
    public void setRunner(InstantRunner r) {
        runner = r;
    }

    protected ParseResult parseArguments(ArgumentParser p) {
        p.addStandardOptions();
        ValueOption<String> host = p.add(ValueOption.of(String.class,
            "host", 'h', "Host to bind to").defaultsTo("*"));
        ValueArgument<Integer> port = p.add(ValueArgument.of(Integer.class,
            "port", "Port to bind to").defaultsTo(8080));
        ValueOption<File> webroot = p.add(ValueOption.of(File.class,
            "webroot", 'r', "Path containing static directories")
            .defaultsTo(new File(".")));
        ValueOption<String> httpLog = p.add(ValueOption.of(String.class,
            "http-log", null, "Log file for HTTP requests").defaultsTo("-")
            .withPlaceholder("<path>"));
        ValueOption<String> debugLog = p.add(ValueOption.of(String.class,
            "debug-log", null, "Log file for debugging").defaultsTo("-")
            .withPlaceholder("<path>"));
        ValueOption<String> logLevel = p.add(ValueOption.of(String.class,
            "log-level", 'L', "Logging level").defaultsTo("INFO"));
        ValueOption<String> cmd = p.add(ValueOption.of(String.class,
            "startup-cmd", 'c', "OS command to run before entering " +
            "main loop"));
        ParseResult r = parseArgumentsInner(p);
        String hostval = r.get(host);
        if (hostval.equals("*")) hostval = null;
        runner.setHost(hostval);
        runner.setPort(r.get(port));
        runner.setWebroot(r.get(webroot));
        runner.setHTTPLog(resolveLogFile(r.get(httpLog)));
        Logging.redirectToStream(resolveLogFile(r.get(debugLog)));
        Logging.setLevel(Level.parse(r.get(logLevel)));
        startupCmd = r.get(cmd);
        return r;
    }
    protected ParseResult parseArgumentsInner(ArgumentParser p) {
        try {
            return p.parse(args);
        } catch (ParseException exc) {
            System.err.println(exc.getMessage());
            System.exit(1);
            return null;
        }
    }

    public void run() {
        Logging.captureExceptions(LOGGER);
        String version = VERSION;
        if (FINE_VERSION != null) version += " (" + FINE_VERSION + ")";
        parseArguments(new ArgumentParser(APPNAME, version));
        LOGGER.info(APPNAME + " " + version);
        runner.addFileAlias("/", "/pages/main.html");
        runner.addFileAlias("/favicon.ico",
                            "/static/logo-static_128x128.ico");
        runner.addFileAlias(Pattern.compile("/([^/]+)\\.html"),
                            "/pages/\\1.html");
        runner.addFileAlias(Pattern.compile("/room/" + ROOM_RE + "/"),
                            "/static/room.html");
        runner.addContentType(".*\\.html", "text/html; charset=utf-8");
        runner.addContentType(".*\\.css", "text/css; charset=utf-8");
        runner.addContentType(".*\\.js", "application/javascript; " +
            "charset=utf-8");
        runner.addContentType(".*\\.svg", "image/svg+xml; charset=utf-8");
        runner.addContentType(".*\\.png", "image/png");
        runner.addContentType(".*\\.ico", "image/vnd.microsoft.icon");
        runner.addRedirect(Pattern.compile("/room/" + ROOM_RE), "\\0/", 301);
        runner.addSyntheticFile("/static/version.js", VERSION_FILE);
        FSResourceProducer prod = runner.makeSourceFiles();
        prod.whitelist("/pages/.*");
        prod.whitelist("/static/.*");
        APIWebSocketHook ws = runner.makeAPIHook();
        ws.getWhitelist().add(Pattern.compile("/room/(" + ROOM_RE + ")/ws"),
                              "\\1");
        ws.getWhitelist().add("/api/ws", "");
        if (startupCmd != null) runCommand(startupCmd);
        LOGGER.info("Running...");
        InstantWebSocketServer srv = runner.makeServer();
        srv.spawn();
    }

    private static PrintStream resolveLogFile(String path) {
        if (path == null || path.equals("-")) {
            return System.err;
        } else {
            try {
                return new PrintStream(new FileOutputStream(path, true),
                                       true);
            } catch (FileNotFoundException exc) {
                // Should not happen.
                throw new RuntimeException(exc);
            }
        }
    }

    private static int runCommand(String cmdline) {
        ProcessBuilder pb = new ProcessBuilder(cmdline.trim().split("\\s+"));
        pb.redirectInput(ProcessBuilder.Redirect.INHERIT);
        pb.redirectOutput(ProcessBuilder.Redirect.INHERIT);
        pb.redirectError(ProcessBuilder.Redirect.INHERIT);
        try {
            Process p = pb.start();
            return p.waitFor();
        } catch (IOException exc) {
            return Integer.MIN_VALUE;
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            return Integer.MIN_VALUE + 1;
        }
    }

}
