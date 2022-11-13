# File: scale_flow.py
# File Created: Thursday, 3rd November 2022 9:58:51 pm
# Author: John Lee (jlee88@nd.edu)
# Last Modified: Sunday, 13th November 2022 5:25:08 pm
# Modified By: John Lee (jlee88@nd.edu>)
# 
# Description: Convert's Ingrid's flow to a normal flow file.

from sgt.core.flow import Inflow
from sgt.utils.parser import Parser
import numpy as np

if __name__ == '__main__':
    
    parser = Parser(desc = 'Scale inflow')
    
    parser.parser.add_argument('-i', help = 'inflow file')
    parser.parser.add_argument('-o', help = 'location to write scaled inflow file to')
    parser.parser.add_argument('-bpm', type= float, help = 'bpm of individual ')
    parser.parser.add_argument('-invert', action = 'store_true', default = False, help = 'flag to compute invert values for 3D to 0D conversion')
    
    args = parser.parse_args()
    
    i = np.loadtxt(args.i,skiprows = 1)
    i = i[:, [0,2]]
    # convert to start from 0 and convert to seconds
    i[:, 0] = np.linspace(0, (60 / args.bpm), len(i))
    
    # get inflow
    inflow = Inflow(i, smooth=False, inverse=args.invert)
    print("CO:", inflow.mean_inflow * 60/1000)
    
    inflow.plot_flow(args.o + '.png')
    inflow.write_flow(args.o)