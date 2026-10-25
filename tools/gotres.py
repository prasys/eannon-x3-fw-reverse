# usage: gotres.py <elf> <base> name:litGOT:pc:litA[,litB...] ...
# resolves GOT-relative symbol refs: slot = (rd(litGOT)+pc) + rd(litX)
import sys,struct,subprocess
elf=sys.argv[1]; B=int(sys.argv[2],16); d=open(elf,'rb').read()
rel={}
for l in subprocess.run(['objdump','-R',elf],capture_output=True,text=True).stdout.splitlines():
    p=l.split()
    if len(p)>=3 and all(c in '0123456789abcdef' for c in p[0]): rel[int(p[0],16)]=p[-1]
def rd(v): return struct.unpack_from('<I',d,v-B)[0]
for spec in sys.argv[3:]:
    name,lg,pc,lits=spec.split(':'); got=(rd(int(lg,16))+int(pc,16))&0xffffffff
    out=[]
    for x in lits.split(','):
        slot=(got+rd(int(x,16)))&0xffffffff; slot-=B
        s=rel.get(slot,'?%#x'%slot)
        out.append(subprocess.run(['c++filt',s],capture_output=True,text=True).stdout.strip())
    print(f'{name:28} key={out[0]:45} group={out[1] if len(out)>1 else ""}')
