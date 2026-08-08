import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;

public class FindWrites extends GhidraScript {
    public void run() throws Exception {
        // find functions writing to offsets 0x154, 0x1483, 0x168c, 0x1690 (potential)
        // scan all instructions for 'MOV dword ptr [reg + 0x1483]' style - too broad.
        // Instead: xrefs to the string "X-Sonos-Device-Id"
        Address addr = findString("X-Sonos-Device-Id");
        if (addr == null) { println("string not found"); return; }
        println("string at " + addr);
        for (Reference r : currentProgram.getReferenceManager().getReferencesTo(addr)) {
            println("  xref from " + r.getFromAddress());
        }
    }
    private Address findString(String s) {
        Memory mem = currentProgram.getMemory();
        // search each defined data block
        var iter = currentProgram.getListing().getDefinedData(true);
        while (iter.hasNext()) {
            var d = iter.next();
            if (d.hasStringValue()) {
                String v = d.getValue().toString();
                if (s.equals(v)) return d.getAddress();
            }
        }
        return null;
    }
}
