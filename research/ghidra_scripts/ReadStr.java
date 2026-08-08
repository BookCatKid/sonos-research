import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.*;

public class ReadStr extends GhidraScript {
    public void run() throws Exception {
        String[] addrs = {"1015d2a00"};
        for (String a : addrs) {
            Address ad = currentProgram.getAddressFactory().getAddress(a);
            // read up to 256 bytes as string
            byte[] b = new byte[256];
            try {
                int n = currentProgram.getMemory().getBytes(ad, b);
                println(a + " len=" + n);
                StringBuilder sb=new StringBuilder();
                for (int i=0;i<Math.min(128,n);i++) {
                    if (b[i]==0) break;
                    sb.append((char)(b[i]&0xff));
                }
                println("str=[" + sb + "]");
            } catch (Exception e) { println(a+" ERR "+e); }
        }
    }
}
