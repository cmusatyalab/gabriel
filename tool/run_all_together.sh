#!/bin/bash
rm ./log/*
rm ./log-err/*

parallel-ssh -p 20 -h ./cloudlet-control -t 0 -o ./log/ -e ./log-err/ 'rm ./monitor-*' > /dev/null &
parallel-ssh -p 20 -h ./cloudlet-control -t 0 -o ./log/ -e ./log-err/ './run.sh' &
sleep 3
parallel-ssh -p 20 -h ./cloudlet-master -t 0 -o ./log/ -e ./log-err/ 'rm ./monitor-*' > /dev/null &
parallel-ssh -p 20 -h ./cloudlet-master -t 0 -o ./log/ -e ./log-err/ './run.sh' &
sleep 3
parallel-ssh -p 20 -h ./cloudlet-engines -t 0 -o ./log/ -e ./log-err/ 'rm ./monitor-*' > /dev/null &
parallel-ssh -p 20 -h ./cloudlet-engines -t 0 -o ./log/ -e ./log-err/ './run.sh'

