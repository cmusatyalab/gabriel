sudo apt-get install python-virtualenv

cd Indexer/pstf/
virtualenv --system-site-packages env 
. env/bin/activate

cd src/
python ./setup.py build
sudo apt-get install libsvm-dev python-libsvm
sudo apt-get install libopencv-dev python-opencv

python ./setup.py install

cd ..
#deactivate 


cd ../..
