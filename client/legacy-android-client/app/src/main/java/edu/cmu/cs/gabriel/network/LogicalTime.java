package edu.cmu.cs.gabriel.network;

import android.util.Pair;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.concurrent.atomic.AtomicInteger;

import edu.cmu.cs.gabriel.Const;

public class LogicalTime {
    public AtomicInteger imageTime; // in # of frames
    public double audioTime; // in seconds
    public long accTime; // in milliseconds, starting from random timestamp

    private ArrayList<Pair<Integer, Long>> accImageMapping = null;
    private int accImageMappingIdx = 0;

    public LogicalTime() {
        this.imageTime = new AtomicInteger(0);
        this.audioTime = 0;
        this.accTime = -1;

        if (Const.IMAGE_TIMING_FILE.exists()) {
            try {
                accImageMapping = new ArrayList<Pair<Integer, Long>>();

                BufferedReader br = new BufferedReader(new FileReader(Const.IMAGE_TIMING_FILE));
                String line = null;
                while ((line = br.readLine()) != null) {
                    String tokens[] = line.split(",");
                    int frame_id = Integer.parseInt(tokens[0]);
                    long timestamp = Long.parseLong(tokens[1]);
                    accImageMapping.add(new Pair(frame_id, timestamp));
                }
            } catch (IOException e) {
            }
        }
    }

    public void increaseAudioTime(double n) { // n is in seconds
        this.audioTime += n;
        this.imageTime.set((int) (this.audioTime * 15));
    }

    public void increaseImageTime(int n) { // n is in # of frames
        this.imageTime.getAndAdd(n);
        this.audioTime = this.imageTime.doubleValue() / 15;
    }

    public void increaseAccTime(long timeDelta) {
        this.updateAccTime(this.accTime + timeDelta);
    }

    public void updateAccTime(long timestamp) {
        if (accImageMapping == null) {
            this.accTime = timestamp;
            return;
        }

        int counter = 0;
        if (timestamp < this.accTime) {
            this.accTime = timestamp;
            counter = accImageMapping.size() - accImageMappingIdx;
            accImageMappingIdx = 0;
        } else {
            this.accTime = timestamp;
            while (accImageMapping.get(accImageMappingIdx).second < timestamp) {
                accImageMappingIdx++;
                counter++;
                if (accImageMappingIdx == accImageMapping.size()) {
                    accImageMappingIdx--;
                    counter--;
                    break;
                }
            }
        }
        this.imageTime.getAndAdd(counter);
    }
}
