// Decompile explicitly supplied function addresses.
// @category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

public class DecompileAddresses extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            throw new IllegalArgumentException("Expected output file followed by addresses");
        }
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try (PrintWriter output = new PrintWriter(args[0], StandardCharsets.UTF_8)) {
            for (int index = 1; index < args.length; index++) {
                Address address = toAddr(args[index]);
                Function function = getFunctionAt(address);
                output.println("================================================================================");
                output.println("Requested: " + address);
                if (function == null) {
                    output.println("No function at address");
                    continue;
                }
                output.println("Function: " + function.getName(true));
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
