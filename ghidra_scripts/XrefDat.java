import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Program;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.symbol.FlowType;

public class XrefDat extends GhidraScript {
    public void run() throws Exception {
        Address ad = currentProgram.getAddressFactory().getAddress("1015d29f0");
        ReferenceManager rm = currentProgram.getReferenceManager();
        int n=0;
        for (Reference r : rm.getReferencesTo(ad)) {
            println("xref from " + r.getFromAddress());
            if (++n>40) break;
        }
    }
}
