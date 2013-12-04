The source code has been modified per this post ie. baud rate
changed from 9600 to 115200

    https://www.wattsupmeters.com/forum/index.php?topic=8.0

To compile the binary type

    gcc -o wattsup wattsup.c

Sample usage is as follows

./wattsup -c 1 ttyUSB0 watts

This will connect to WattsUp once and output the watt usage

Binary has been provided. It was compiled under Centos 5 however it should
be usable under most modern Linux 2.6+ systems. I tested it under Ubuntu 
8.04 and works just fine. Use at your own risk otherwise compile from 
source.
