#!/bin/bash
parallel-ssh -p 20 -h ./cloudlet-control -t 0 -o ./log/ -e ./log/ './run.sh' &
sleep 3
parallel-ssh -p 20 -h ./cloudlet-engines -t 0 -o ./log/ -e ./log/ './run.sh'

