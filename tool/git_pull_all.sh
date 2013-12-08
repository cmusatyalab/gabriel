#!/bin/bash
parallel-ssh -P -h ./cloudlet-control -o /tmp/ 'cd gabriel && git pull'
parallel-ssh -P -h ./cloudlet-master -o /tmp/ 'cd gabriel && git pull'
parallel-ssh -P -h ./cloudlet-engines -o /tmp/ 'cd gabriel && git pull'
