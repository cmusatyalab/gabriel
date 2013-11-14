#!/usr/bin/env python
import numpy as np

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
    battery_offload_list = []
    battery_native_list = []
    latency_offload_list = []
    latency_native_list = []
    for exp_idx in xrange(1, 6):
        print "Experiment %d:" % exp_idx
        # calculate battery usage for offloading
        battery_offload = calculate_energy("battery_offload_%d.txt" % exp_idx, 300)
        print "average offloading energy: %f joule (J)" % battery_offload
        battery_offload_list.append(battery_offload)
        # calculate battery usage for native
        battery_native = calculate_energy("battery_native_%d.txt" % exp_idx, 300)
        print "average native energy: %f joule (J)" % battery_native
        battery_native_list.append(battery_native)

        # calculate average latency for offloading
        latency_offload = calculate_latency("latency_offload_glass_%d.txt" % exp_idx)
        print "offloading average latency: %f seconds" % latency_offload
        latency_offload_list.append(latency_offload)
        # calculate average latency for native
        latency_native = calculate_latency("latency_native_glass_%d.txt" % exp_idx)
        print "native average latency: %f seconds" % latency_native
        latency_native_list.append(latency_native)

        print

    battery_offload_mean = np.mean(battery_offload_list)
    battery_native_mean = np.mean(battery_native_list)
    latency_offload_mean = np.mean(latency_offload_list)
    latency_native_mean = np.mean(latency_native_list)

    battery_offload_std = np.std(battery_offload_list)
    battery_native_std = np.std(battery_native_list)
    latency_offload_std = np.std(latency_offload_list)
    latency_native_std = np.std(latency_native_list)

    print "Summary:"
    print "offloading energy mean: %f joule (J), std: %f joule (J)" % (battery_offload_mean, battery_offload_std)
    print "native energy mean: %f joule (J), std: %f joule (J)" % (battery_native_mean, battery_native_std)
    print "offloading latency mean: %f seconds, std: %f seconds" % (latency_offload_mean, latency_offload_std)
    print "native latency mean: %f seconds, std: %f seconds" % (latency_native_mean, latency_native_std)
    print

if __name__ == "__main__":
    main()
