#!/usr/bin/env python

def calculate_energy(file_name, frame_n):
    last_t = -1
    energy = 0
    with open(file_name) as f:
        for line in f:
            if line[0].isalpha():
                continue
            t, current, voltage = line.strip().split()
            t = int(t)
            current = float(current)
            voltage = float(voltage)
            #print (t, current, voltage)
            if last_t > 0:
                energy += current * voltage * (t - last_t)
            last_t = t
    energy = energy / 1000000 # convert to J
    ave_energy = energy / frame_n

    return ave_energy

def calculate_latency(file_name):
    # input line example: 1, 1383937061819, 1383937061862, 43, 43
    ave_latency = 0
    frame_n = 0
    with open(file_name) as f:
        for line in f:
            frame_n, start_time, end_time, latency, jitter = line.strip().split(',')
            frame_n = int(frame_n)
            latency = int(latency) / 1000.0 # convert to second
            ave_latency += latency
    ave_latency = float(ave_latency) / frame_n
    
    return ave_latency 

def main():
    # calculate battery usage for offloading
    print "average offloading energy: %f joule (J)" % calculate_energy("battery_offload.txt", 300)
    # calculate battery usage for native
    print "average native energy: %f joule (J)" % calculate_energy("battery_native.txt", 300)
    print

    # calculate average latency for offloading
    print "offloading average latency: %f seconds" % calculate_latency("latency_offload_glass.txt")
    # calculate average latency for native
    print "native average latency: %f seconds" % calculate_latency("latency_native_glass.txt")
    print

if __name__ == "__main__":
    main()
