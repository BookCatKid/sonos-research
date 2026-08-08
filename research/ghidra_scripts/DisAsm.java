import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;

public class DisAsm extends GhidraScript {
    public void run() throws Exception {
        Listing listing = currentProgram.getListing();
        for (String a : new String[]{"100e235a0"}) {
            Address ad = currentProgram.getAddressFactory().getAddress(a);
            println("=== disasm around " + a + " (ref to 1015d2a00) ===");
            InstructionIterator it = listing.getInstructions(ad, true);
            while (it.hasNext()) {
                Instruction ins = it.next();
                String s = ins.toString();
                if (s.contains("1015d2a00") || s.contains("1015d29")) println(s);
                if (ins.getAddress().getOffset() > 0x100e23630L) break;
            }
        }
    }
}
