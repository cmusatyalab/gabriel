#!/bin/bash
parallel-ssh -P -h ./machine-list -o /tmp/ 'cd gabriel && git pull'
