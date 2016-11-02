using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Windows.Storage;

namespace gabriel_client
{
    class MyLogger
    {
        string _resultFileName;
        StorageFile _resultFile;

        public MyLogger(string resultFileName)
        {
            _resultFileName = resultFileName;
        }

        public async Task InitializeLogger()
        {
            if (Const.IS_EXPERIMENT)
            {
                StorageFolder EXP_DIR = await Const.ROOT_DIR.CreateFolderAsync(Const.EXP_DIR_NAME, CreationCollisionOption.OpenIfExists);
                _resultFile = await EXP_DIR.CreateFileAsync(_resultFileName, CreationCollisionOption.ReplaceExisting);
                await FileIO.WriteTextAsync(_resultFile, "FrameID\tEngineID\tStartTime\tCompressedTime\tRecvTime\tDoneTime\tStatus\n");
            }
        }

        public async Task WriteString(string str)
        {
            if (Const.IS_EXPERIMENT)
            {
                await FileIO.AppendTextAsync(_resultFile, str);
            }
        }
    }
}
