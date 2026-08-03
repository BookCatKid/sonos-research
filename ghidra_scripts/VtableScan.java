import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.*;

public class VtableScan extends GhidraScript {
    public void run() throws Exception {
        String[] bases = {"1013c3bb8","1013c3c08","1013c3c48","1013c3cb0","1013c3cd8","1013c3e88"};
        for (String b : bases) {
            Address a = currentProgram.getAddressFactory().getAddress(b);
            println("=== vtable @ " + b + " ===");
            for (int i=0;i<40;i++) {
                long p = currentProgram.getMemory().getLong(a.add(i*8));
                if (p == 0) continue;
                Function f = currentProgram.getFunctionManager().getFunctionAt(currentProgram.getAddressFactory().getAddress(Long.toHexString(p)));
                println(String.format("  slot %2d 0x%x -> %s", i, p, (f!=null? f.getName()+"@"+f.getEntryPoint() : "? (0x%x)".formatted(p))));
            }
        }
    }
}
