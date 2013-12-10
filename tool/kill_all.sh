#!/bin/bash
parallel-ssh -P -p 20 -h ./cloudlet-control -t 0 'killall python'
parallel-ssh -P -p 20 -h ./cloudlet-control -t 0 'killall java'
parallel-ssh -P -p 20 -h ./cloudlet-master -t 0 'killall python'
parallel-ssh -P -p 20 -h ./cloudlet-master -t 0 'killall java'
parallel-ssh -P -p 20 -h ./cloudlet-engines -t 0 'killall python'
parallel-ssh -P -p 20 -h ./cloudlet-engines -t 0 'killall java'

