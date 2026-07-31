// Decompile functions containing any supplied scalar constant.
//@category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

public class ScalarXrefs extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            throw new IllegalArgumentException("Expected output file followed by hex constants");
        }
        Map<Long, String> targets = new LinkedHashMap<>();
        for (int i = 1; i < args.length; i++) {
            targets.put(Long.parseUnsignedLong(args[i].replaceFirst("^0x", ""), 16), args[i]);
        }
        Map<Function, Set<String>> matches = new LinkedHashMap<>();
        for (Instruction instruction : currentProgram.getListing().getInstructions(true)) {
            for (int operand = 0; operand < instruction.getNumOperands(); operand++) {
                for (Object object : instruction.getOpObjects(operand)) {
                    if (!(object instanceof Scalar)) continue;
                    long value = ((Scalar) object).getUnsignedValue();
                    if (!targets.containsKey(value)) continue;
                    Function function = getFunctionContaining(instruction.getAddress());
                    if (function != null) {
                        matches.computeIfAbsent(function, ignored -> new LinkedHashSet<>()).add(targets.get(value));
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
                output.println("Constants: " + String.join(", ", entry.getValue()));
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
