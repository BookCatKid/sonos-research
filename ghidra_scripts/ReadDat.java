import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.*;
import ghidra.program.model.data.*;

public class ReadDat extends GhidraScript {
    public void run() throws Exception {
        String[] addrs = {"1015d2a00"};
        Memory mem = currentProgram.getMemory();
        for (String a : addrs) {
            Address ad = currentProgram.getAddressFactory().getAddress(a);
            Data d = currentProgram.getListing().getDataAt(ad);
            println(a + " type=" + (d!=null? d.getDataType().getName() : "?"));
            byte[] b = new byte[256];
            int n = mem.getBytes(ad, b);
            println("bytes: " + toHex(b, 64));
            // try string
            for (int i=0;i<128;i++) {
                if (b[i]==0) { println("str: " + new String(b,0,i,"UTF-8")); break; }
            }
        }
    }
    String toHex(byte[] b, int max) {
        StringBuilder sb=new StringBuilder();
        for (int i=0;i<Math.min(max,b.length);i++) sb.append(String.format("%02x ", b[i]));
        return sb.toString();
    }
}
