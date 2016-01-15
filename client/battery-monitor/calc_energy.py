import sys
fn = sys.argv[1]
print "Energy result for", fn
f = open(fn, 'r')
lastts = 0
energy = 0
totalT = 0
for line in f:
    timestamp, I, V = line.split("\t") # ms, mA, V
    I = float(I) / 1000
    time = int(timestamp) - lastts
    if lastts  == 0 :
       time = 0

    time = time / 1000.0 # sec
    energy += float(I) * float(V) * time
    lastts = int(timestamp)
    totalT += time

print "Energy/J:", energy
print "Total time/s:",  totalT
print "Power/W:", energy / totalT

