package net.instant;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.URL;
import java.util.jar.Manifest;
import java.util.regex.Pattern;
import net.instant.hooks.Error404Hook;
import net.instant.hooks.RoomWebSocketHook;
import net.instant.hooks.RedirectHook;
import net.instant.hooks.StaticFileHook;
import net.instant.proto.MessageDistributor;
import net.instant.util.StringSigner;
import net.instant.util.Util;
import net.instant.util.argparse.ArgParser;
import net.instant.util.argparse.IntegerOption;
import net.instant.util.argparse.ParseException;
import net.instant.util.argparse.ParseResult;
import net.instant.util.argparse.StringOption;
import net.instant.util.fileprod.FileProducer;
import net.instant.util.fileprod.FilesystemProducer;
import net.instant.util.fileprod.ResourceProducer;
import net.instant.util.fileprod.StringProducer;
import net.instant.ws.CookieHandler;
import net.instant.ws.InstantWebSocketServer;
import org.java_websocket.server.WebSocketServer;

public class Main implements Runnable {

    public static final String VERSION = "1.3.2";
    public static final String ROOM_RE =
        "[a-zA-Z](?:[a-zA-Z0-9_-]*[a-zA-Z0-9])?";
    public static final String STAGING_RE = "[a-zA-Z0-9-]+";
    public static final String FINE_VERSION;

    private static final String VERSION_FILE;

    static {
        System.setProperty("java.util.logging.SimpleFormatter.format",
                           "[%1$tY-%1$tm-%1$td %1$tH:%1$tM:%1$tS.%1$tL " +
                           "%4$-7s %3$-20s] %5$s %6$s%n");
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
            Util.escapeJSString(VERSION, true),
            Util.escapeJSString(FINE_VERSION, true));
    }

    private final String[] args;
    private InstantRunner runner;

    public Main(String[] args) {
        this.args = args;
        this.runner = new InstantRunner();
    }

    public int getArguments() {
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

    protected void parseArguments() {
        ArgParser p = new ArgParser("Instant");
        p.addHelp();
        StringOption host = p.add(new StringOption("host", false, "*"),
                                  "Host to bind to");
        IntegerOption port = p.add(new IntegerOption("port", true, 8080),
                                   "Port number to use");
        ParseResult r;
        try {
            r = p.parse(args);
        } catch (ParseException exc) {
            System.err.println(exc.getMessage());
            System.exit(1);
            return;
        }
        String hostName = host.get(r);
        if (hostName.equals("*")) hostName = "";
        runner.setHost(hostName);
        runner.setPort(port.get(r));
    }

    public void run() {
        parseArguments();
        String signaturePath = Util.getConfiguration(
            "instant.cookies.keyfile");
        StringSigner signer = null;
        if (signaturePath != null)
            signer = StringSigner.getInstance(new File(signaturePath));
        if (signer == null)
            signer = StringSigner.getInstance(Util.getRandomness(64));
        runner.addFileAlias("/", "/pages/main.html");
        runner.addFileAlias("/favicon.ico",
                            "/static/logo-static_128x128.ico");
        runner.addFileAlias(Pattern.compile("/([^/]+\\.html)"),
                            "/pages/\\1");
        runner.addFileAlias(Pattern.compile("/room/" + ROOM_RE + "/"),
                            "/static/room.html");
        runner.addFileAlias(Pattern.compile("/(" + STAGING_RE + ")/"),
                            "/static/\\1/main.html");
        runner.addFileAlias(Pattern.compile("/(" + STAGING_RE + ")/room/" +
                            ROOM_RE + "/"), "/static/\\1/room.html");
        runner.addContentType(".*\\.html", "text/html; charset=utf-8");
        runner.addContentType(".*\\.css", "text/css; charset=utf-8");
        runner.addContentType(".*\\.js", "application/javascript; " +
                               "charset=utf-8");
        runner.addContentType(".*\\.svg", "image/svg+xml; charset=utf-8");
        runner.addContentType(".*\\.png", "image/png");
        runner.addContentType(".*\\.ico", "image/vnd.microsoft.icon");
        runner.addRedirect(Pattern.compile("/room/(" + ROOM_RE + ")"),
                           "/room/\\1/", 301);
        runner.addRedirect(Pattern.compile("/(" + STAGING_RE + ")"), "/\\1/",
                           301);
        runner.addRedirect(Pattern.compile("/(" + STAGING_RE + ")/room/(" +
                           ROOM_RE + ")"), "/\\1/room/\\2/", 301);
        InstantWebSocketServer srv = runner.make();
        srv.setCookieHandler(new CookieHandler(signer));
        FileProducer prod = runner.getFileHook().getProducer();
        prod.addProducer(new FilesystemProducer(".", ""));
        prod.addProducer(new ResourceProducer());
        prod.whitelist("/static/.*");
        prod.whitelist("/pages/.*");
        runner.getStringProducer().addFile("/static/version.js",
                                           VERSION_FILE);
        srv.addInternalHook(new Error404Hook());
        srv.run();
    }

}
