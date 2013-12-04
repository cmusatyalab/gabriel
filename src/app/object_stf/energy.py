'''
Code came from Kiryong Ha
Modified by Wenlu Hu
'''
WATTS_BIN = "~/Development/gabriel/src/power/wattsup"

import sys
from datetime import datetime
import time
sys.path.insert(0, "../../")
from control import log as logging
import threading

LOG = logging.getLogger(__name__)

class EnergyRecordingThread(threading.Thread):
    def __init__(self, outfile):
        self.is_stop_thread = False
        self.outfile = outfile
        threading.Thread.__init__(self)

    def run(self):
        LOG.info("Starting to record energy data")
        self.energy_measurement(self.outfile)

    def stop(self):
        self.is_stop_thread = True

    def energy_measurement(self, power_out_file):
        
        global last_average_power

        # Start WattsUP through SSH
        command = "sudo %s /dev/ttyUSB0" % WATTS_BIN
        print command
    #    ssh_stdin, ssh_stdout, ssh_stderr = ssh.exec_command(command)
        import subprocess
        p = subprocess.Popen(command, shell = True, bufsize=1, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        start_time = datetime.now()
        power_sum = 0.0
        power_counter = 0
        power_log = open(power_out_file, "w")
        power_log_sum = open(power_out_file + ".sum", "w")
        while self.is_stop_thread == False:
            # ret = ssh_stdout.readline()
            ret = p.stdout.readline()
            if not ret:
                continue
            print ret
            power_value = float(ret.split(",")[0])
            if power_value == 0.0:
                continue
    #        if power_value < 1.0 or power_value > 30.0:
    #            print "Error at Power Measurement with %f" % (power_value)
    #            sys.exit(1)
            power_log.write("%s\t%s" % (str(datetime.now()), ret))
            print "current power : %f" % power_value
            power_sum = power_sum + power_value
            power_counter = power_counter + 1
            time.sleep(0.1)

        # Stop WattsUP through SSH
        end_time = datetime.now()
        if power_counter == 0:
            power_counter = 1
        message = "%s\t%f\t(%f/%d)" % (str(end_time-start_time), power_sum/power_counter, power_sum, power_counter)
        power_log_sum.write(message)
        power_log.close()
        power_log_sum.close()
        last_average_power = power_sum/power_counter
        print "Average Power for %s: %s" % (power_out_file, message)

        return 0

if __name__ == "__main__":
    energyThread =  EnergyRecordingThread("energy_log")
    energyThread.start()
    time.sleep(20)
    energyThread.stop()

