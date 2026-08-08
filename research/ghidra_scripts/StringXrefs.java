// Decompile functions referencing any explicitly supplied string marker.
// @category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

public class StringXrefs extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            throw new IllegalArgumentException("Expected output file followed by string markers");
        }

        Listing listing = currentProgram.getListing();
        Map<Function, Set<String>> matches = new LinkedHashMap<>();
        DataIterator data = listing.getDefinedData(true);
        while (data.hasNext() && !monitor.isCancelled()) {
            Data item = data.next();
            Object value = item.getValue();
            if (!(value instanceof String)) {
                continue;
            }
            String text = (String) value;
            for (int index = 1; index < args.length; index++) {
                if (!text.contains(args[index])) {
                    continue;
                }
                ReferenceIterator refs = currentProgram.getReferenceManager().getReferencesTo(item.getAddress());
                while (refs.hasNext()) {
                    Reference ref = refs.next();
                    Function function = listing.getFunctionContaining(ref.getFromAddress());
                    if (function != null) {
                        matches.computeIfAbsent(function, ignored -> new LinkedHashSet<>()).add(args[index]);
                    }
                }
            }
        }

        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try (PrintWriter output = new PrintWriter(args[0], StandardCharsets.UTF_8)) {
            output.println("Matched functions: " + matches.size());
            for (Map.Entry<Function, Set<String>> entry : matches.entrySet()) {
                Function function = entry.getKey();
                output.println("================================================================================");
                output.println("Function: " + function.getName(true));
                output.println("Entry: " + function.getEntryPoint());
                output.println("Markers: " + String.join(", ", entry.getValue()));
                DecompileResults results = decompiler.decompileFunction(function, 120, monitor);
                if (results.decompileCompleted() && results.getDecompiledFunction() != null) {
                    output.println(results.getDecompiledFunction().getC());
                } else {
                    output.println("DECOMPILE FAILED: " + results.getErrorMessage());
                }
            }
        } finally {
            decompiler.dispose();
        }
    }
}
