import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.*;

public class ResolveVtable extends GhidraScript {
    public void run() throws Exception {
        // read pointer DAT at 1013a41c0 then add 0x10 and dump slots
        Memory mem = currentProgram.getMemory();
        Address d = currentProgram.getAddressFactory().getAddress("1013a41c0");
        long v = mem.getLong(d);
        println("PTR_DAT_1013a41c0 = 0x" + Long.toHexString(v));
        Address va = currentProgram.getAddressFactory().getAddress(Long.toHexString(v + 0x10));
        println("vtable starts at 0x" + Long.toHexString(v+0x10));
        for (int i=0;i<48;i++) {
            long p = mem.getLong(va.add(i*8));
            if (p==0 || p>0x200000000L) { println(String.format("  slot %2d 0x%x (non-code)", i, p)); continue; }
            Function f = currentProgram.getFunctionManager().getFunctionAt(currentProgram.getAddressFactory().getAddress(Long.toHexString(p)));
            println(String.format("  slot %2d -> 0x%x %s", i, p, (f!=null? f.getName()+"@"+f.getEntryPoint() : "?")));
        }
    }
}
