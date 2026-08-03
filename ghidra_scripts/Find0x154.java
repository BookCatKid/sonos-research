import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.symbol.*;
import ghidra.program.model.lang.*;
import ghidra.program.model.scalar.Scalar;

public class Find0x154 extends GhidraScript {
    public void run() throws Exception {
        int count=0;
        Listing listing = currentProgram.getListing();
        InstructionIterator it = listing.getInstructions(true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            int nops = ins.getNumOperands();
            for (int i=0;i<nops;i++) {
                Object[] objs = ins.getOpObjects(i);
                for (Object o : objs) {
                    if (o instanceof Scalar) {
                        long v = ((Scalar)o).getValue();
                        if (v == 0x154 || v == 0x1483) {
                            println(ins.getAddress() + " " + ins.toString());
                            count++;
                        }
                    }
                }
            }
        }
        println("total " + count);
    }
}
