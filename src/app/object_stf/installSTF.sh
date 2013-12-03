sudo apt-get install python-virtualenv

sudo apt-get install libsvm-dev python-libsvm
sudo apt-get install libopencv-dev python-opencv
sudo apt-get install python-numpy

cd Indexer/pstf/
virtualenv --system-site-packages env 
. env/bin/activate #might need to do this manually before install

cd src/
python ./setup.py build

python ./setup.py install

cd ..
#deactivate 


cd ../..
