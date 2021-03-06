package net.instant.util.argparse;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class OptionDispatcher implements Processor {

    private final Map<String, Processor> options;
    private final Map<Character, Processor> shortOptions;
    private final List<Processor> arguments;
    private String name;
    private Iterator<Processor> nextArgument;
    private boolean argumentsOnly;

    public OptionDispatcher(String name) {
        this.options = new LinkedHashMap<String, Processor>();
        this.shortOptions = new LinkedHashMap<Character, Processor>();
        this.arguments = new ArrayList<Processor>();
        this.name = name;
    }

    public Map<String, Processor> getOptions() {
        return options;
    }

    public Map<Character, Processor> getShortOptions() {
        return shortOptions;
    }

    public List<Processor> getArguments() {
        return arguments;
    }

    public List<Processor> getAllOptions() {
        List<Processor> ret = new ArrayList<Processor>();
        ret.addAll(getOptions().values());
        ret.addAll(getArguments());
        return ret;
    }

    public String getName() {
        return name;
    }
    public void setName(String n) {
        name = n;
    }

    public void addOption(BaseOption<?> opt) {
        options.put(opt.getName(), opt);
        if (opt.getShortName() != null)
            shortOptions.put(opt.getShortName(), opt);
    }
    public void addArgument(Processor arg) {
        arguments.add(arg);
    }
    public void remove(Processor proc) {
        options.remove(proc.getName());
        if (proc instanceof BaseOption<?>)
            shortOptions.remove(((BaseOption<?>) proc).getShortName());
        arguments.remove(proc);
    }

    public Processor getOption(ArgumentSplitter.ArgValue av) {
        switch (av.getType()) {
            case SHORT_OPTION:
                return shortOptions.get(av.getValue().charAt(0));
            case LONG_OPTION:
                return options.get(av.getValue());
        }
        throw new IllegalArgumentException("Trying to resolve " + av +
                                           " as an option");
    }

    public void startParsing(ParseResultBuilder drain)
            throws ParsingException {
        nextArgument = getArguments().iterator();
        argumentsOnly = false;
        for (Processor p : getAllOptions())
            p.startParsing(drain);
    }

    public void parse(ArgumentSplitter source, ParseResultBuilder drain)
            throws ParsingException {
        for (;;) {
            ArgumentSplitter.ArgValue av = source.peek((argumentsOnly) ?
                ArgumentSplitter.Mode.FORCE_ARGUMENTS :
                ArgumentSplitter.Mode.OPTIONS);
            if (av == null) break;
            Processor chain;
            switch (av.getType()) {
                case SHORT_OPTION:
                case LONG_OPTION:
                    chain = getOption(av);
                    break;
                case VALUE:
                    throw new ParsingException("Orphan " + av);
                case ARGUMENT:
                    if (! nextArgument.hasNext()) {
                        throw new ParsingException("Superfluous " + av);
                    }
                    chain = nextArgument.next();
                    if (chain == null)
                        throw new NullPointerException("Null argument " +
                            "processor in OptionDispatcher");
                    break;
                case SPECIAL:
                    if (av.getValue().equals("--")) {
                        argumentsOnly = true;
                        continue;
                    } else {
                        throw new AssertionError("Unrecognized special " +
                            av);
                    }
                default:
                    throw new AssertionError("Unknown argument type " +
                        av.getType() + "?!");
            }
            if (chain == null)
                throw new ParsingException("Unrecognized " + av);
            chain.parse(source, drain);
        }
    }

    public void finishParsing(ParseResultBuilder drain)
            throws ParsingException {
        for (Processor p : getAllOptions())
            p.finishParsing(drain);
        nextArgument = null;
        argumentsOnly = false;
    }

    public String formatName() {
        return getName();
    }

    public String formatUsage() {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (Processor p : getAllOptions()) {
            String pu = p.formatUsage();
            if (pu == null) {
                continue;
            } else if (first) {
                first = false;
            } else {
                sb.append(' ');
            }
            sb.append(pu);
        }
        return (first) ? null : sb.toString();
    }

    public HelpLine getHelpLine() {
        return null;
    }

    private void accumulateHelpLines(Processor p, List<HelpLine> drain) {
        HelpLine h = p.getHelpLine();
        if (h != null) drain.add(h);
    }
    public List<HelpLine> getAllHelpLines() {
        List<HelpLine> ret = new ArrayList<HelpLine>();
        for (Processor p : getAllOptions()) accumulateHelpLines(p, ret);
        return ret;
    }

}
