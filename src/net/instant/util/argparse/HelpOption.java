package net.instant.util.argparse;

import java.util.LinkedList;
import java.util.List;

public class HelpOption extends ActionOption {

    public HelpOption(String name, Character shortName, String help) {
        super(name, shortName, help);
    }
    public HelpOption() {
        this("help", '?', "Display help");
    }

    public void run() {
        if (getParser() == null)
            throw new NullPointerException("Cannot display help " +
                                           "without parser");
        displayHelp(getParser(), 0);
    }

    private static List<BaseOption<?>> sortedOptions(ArgumentParser p) {
        List<BaseOption<?>> ret = new LinkedList<BaseOption<?>>();
        p.getOptions(ret, false);
        p.getOptions(ret, true);
        return ret;
    }

    private static String makeWidth(int w) {
        return (w == 0) ? "" : Integer.toString(-w);
    }

    public static String formatUsage(ArgumentParser p, boolean prefix) {
        StringBuilder sb = new StringBuilder();
        if (prefix) {
            sb.append("USAGE: ");
            if (p.getProgName() == null) {
                sb.append("...");
            } else {
                sb.append(p.getProgName());
            }
        }
        for (BaseOption<?> opt : sortedOptions(p)) {
            if (sb.length() != 0) sb.append(' ');
            sb.append(opt.formatUsage());
        }
        return sb.toString();
    }
    public static String formatDescription(ArgumentParser p) {
        return p.getDescription();
    }
    public static String formatHelp(ArgumentParser p) {
        List<String[]> columns = new LinkedList<String[]>();
        int wdN = 0, wdA = 0;
        for (BaseOption<?> opt : sortedOptions(p)) {
            String[] item = new String[] {
                opt.formatName(), ((opt.isPositional()) ? ":" : ""),
                opt.formatArguments(), opt.formatHelp()
            };
            if (item[2] == null) item[2] = "";
            wdN = Math.max(wdN, item[0].length() + item[1].length());
            wdA = Math.max(wdA, item[2].length());
            columns.add(item);
        }
        String optFormat = String.format("%%%ss%%s%s%%%ss: %%s",
            makeWidth(wdN), ((wdA != 0) ? " " : ""), makeWidth(wdA));
        String argFormat = String.format("%%%ss%%s%s%%%ss: %%s",
            makeWidth(wdN - 1), ((wdA != 0) ? " " : ""), makeWidth(wdA));
        int indentWidth = wdN + wdA + ((wdA != 0) ? 3 : 2);
        // HACK: It will take until Java 11 for string repetition to appear
        //       the standard library...
        String indent = new String(new char[indentWidth]).replace('\0', ' ');
        StringBuilder sb = new StringBuilder();
        for (String[] col : columns) {
            if (sb.length() != 0) sb.append('\n');
            col[3] = col[3].replace("\n", "\n" + indent);
            // Varargs magic!
            sb.append(String.format(((col[1].isEmpty()) ? optFormat :
                argFormat), (Object[]) col));
        }
        return sb.toString();
    }

    public static String formatFullHelp(ArgumentParser p) {
        String usage = formatUsage(p, true), desc = formatDescription(p);
        String help = formatHelp(p);
        StringBuilder sb = new StringBuilder(usage);
        if (desc != null) sb.append('\n').append(desc);
        return sb.append('\n').append(help).toString();
    }

    public static void displayHelp(ArgumentParser p, int code) {
        System.err.println(formatFullHelp(p));
        System.exit(code);
    }

}
