import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;

public class DisAsm2 extends GhidraScript {
    public void run() throws Exception {
        Listing listing = currentProgram.getListing();
        Address ad = currentProgram.getAddressFactory().getAddress("100e235a0");
        println("=== disasm FUN_100e235a0 (full) ===");
        InstructionIterator it = listing.getInstructions(ad, true);
        int n=0;
        while (it.hasNext() && n<400) {
            Instruction ins = it.next();
            println(ins.getAddress() + "  " + ins.toString());
            n++;
        }
    }
}
