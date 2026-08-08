// Decompile functions referencing explicitly supplied data/code addresses.
// @category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

public class XrefsToAddresses extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            throw new IllegalArgumentException("Expected output file followed by addresses");
        }

        Map<Function, Set<String>> matches = new LinkedHashMap<>();
        for (int index = 1; index < args.length; index++) {
            Address target = toAddr(args[index]);
            ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(target);
            while (references.hasNext()) {
                Reference reference = references.next();
                Function function = getFunctionContaining(reference.getFromAddress());
                if (function != null) {
                    matches.computeIfAbsent(function, ignored -> new LinkedHashSet<>())
                        .add(target.toString());
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
                output.println("Targets: " + String.join(", ", entry.getValue()));
                DecompileResults results = decompiler.decompileFunction(function, 90, monitor);
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
