APP_DIR=~/Development/gabriel/src/app/object_stf/Indexer/pstf/src/
CURRENT_DIR=`pwd`
PIC_FN=$1

mv $PIC_FN $APP_DIR

# echo $APP_DIR
cd $APP_DIR

# echo "python -m pstf.scripts.runSTF msrc21_Lab.pred 6 $PIC_FN"
python -m pstf.scripts.runSTF msrc21_Lab.pred 6 $PIC_FN

# echo $CURRENT_DIR
cd $CURRENT_DIR
