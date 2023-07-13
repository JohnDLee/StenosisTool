# File: linear_transform.py
# File Created: Tuesday, 14th February 2023 11:25:35 am
# Author: John Lee (jlee88@nd.edu)
# Last Modified: Thursday, 13th July 2023 1:05:54 pm
# Modified By: John Lee (jlee88@nd.edu>)
# 
# Description:  Perform a linear transform on the junctions
#! ONLY SAVES POSITIVE OR PHYSICAL ONES



#! Only apply global constant to the bc of the partition
#! Change to LPA and RPA only and combine MPA in both
#! Currently replace r in code, so it will overwrite if you try to retune a value.
#! COuld modify resistance before first bifurcation.

#! variable does not make sense, since 1 side gets drastically increased to account for the entire pulmonary increase
#! Not stable, since swapping LPA and RPA order may result in failure.

import argparse

from svinterface.core.zerod.lpn import LPN, OriginalLPN
from svinterface.core.polydata import Centerlines
from svinterface.core.bc import RCR 
from svinterface.core.zerod.solver import Solver0Dcpp, SolverResults
from svinterface.manager.baseManager import Manager
from svinterface.utils.misc import m2d
import numpy as np
from concurrent.futures import ProcessPoolExecutor, wait



def conc_sim(lpn: OriginalLPN, vess: int, junc_id: int, which: int, junction_outlet_vessels: list):
    ''' To run simulations using multi-processing '''
    # get downstream vessel.
    v = lpn.get_vessel(vess)
    # compute a R as 10% and original value
    r = .1 * v['zero_d_element_values']['R_poiseuille'] + lpn.get_junction(junc_id)['junction_values']['R_poiseuille'][which]
    # change to JC
    lpn.change_junction_outlet(junction_id_or_name = junc_id, which = which, R = r)
    # Solver
    tmp = Solver0Dcpp(lpn, last_cycle_only=True, mean_only=True, debug = False)
    tmp_sim = tmp.run_sim()

    # get results
    tmp_sim.convert_to_mmHg()
    pressures_cur = tmp_sim.result_df.iloc[junction_outlet_vessels]['pressure_in'].to_numpy()
    # undo change
    lpn.change_junction_outlet(junction_id_or_name = junc_id, which = which, R = 0)

    return r, pressures_cur     

def linear_transform(zerod_lpn: LPN, threed_c: Centerlines, M: Manager,):
    
    # get relevant positions
    tree = zerod_lpn.get_tree()
    # determine sides
    zerod_lpn.det_lpa_rpa(tree)
    
    for side in  'MPA', 'RPA', 'LPA':
        print(f"Evaluating {side}.")
        linear_transform_side(zerod_lpn, threed_c, M, side)
    
    
def linear_transform_side(zerod_lpn: LPN, threed_c: Centerlines, M: Manager, side: str):

    # get relevant positions
    tree = zerod_lpn.get_tree()
    
    # collect
    junction_outlet_vessels = []
    junction_gids = []
    junction_nodes = []
    for junc_node in zerod_lpn.tree_bfs_iterator(tree, allow='junction'):
        # only get it if the side matches
        if junc_node.vessel_info[0]['side'] == side:
            junction_outlet_vessels += junc_node.vessel_info[0]['outlet_vessels']
            junction_gids += junc_node.vessel_info[0]['gid'][1] # out gids
            junction_nodes.append(junc_node)
    
        
    
    assert len(junction_gids) == len(junction_outlet_vessels), "Number of junction ids on 3D data will not match the number of outlet vessels in the 0D"
        
    
    # extract target pressures.
    target_pressures = threed_c.get_pointdata_array("avg_pressure")[junction_gids + [0]]
    
    # compute initial case
    tmp = Solver0Dcpp(zerod_lpn, last_cycle_only=True, mean_only=True, debug = False)
    init_sim = tmp.run_sim()
    init_sim.convert_to_mmHg()
    pressures_init = init_sim.result_df.iloc[junction_outlet_vessels + [0]]['pressure_in'].to_numpy()
    
    jcs = []
    

        
    # iterate through each junction outlet (submit futures)
    futures = []
    with ProcessPoolExecutor() as executor:
        for junc_node in junction_nodes:
            for idx, vess in enumerate(junc_node.vessel_info[0]['outlet_vessels']):
                print(f"Changing junction {junc_node.id} vessel {idx}.")
                futures.append(executor.submit(conc_sim, zerod_lpn.get_fast_lpn(), vess, junc_node.id, idx, junction_outlet_vessels + [0]))
        pressures = []
        # parse futures in order
        print("Retrieving results...")
        for idx, f in enumerate(futures):
            
            r, ps = f.result()
            pressures.append( ps - pressures_init)
            jcs.append(r)
            print(f"\tRetrieved results for process {idx}/{len(futures)}")
            
    # convert to numpy
    # add constant & transpose
    pressures.append(list(np.ones(len(pressures[0]))))
    pressures = np.array(pressures).T
    
    # solve for a
    press_inv = np.linalg.inv(pressures) 
    
    # get target pressure differences
    target_pressures_diff = target_pressures - pressures_init
    
    # compute alpha values
    aT = press_inv @ target_pressures_diff
    
    
    
    # print(target_pressures_diff)
    # print(aT)
    # np.savetxt(f"temp/target_dp_{side}.txt", target_pressures_diff, delimiter = ' ' )
    # np.savetxt(f"temp/aT_{side}.txt", aT, delimiter = ' ')
    
    
    # iterate through each junction outlet and fill it with appropriate junction values
    counter = 0
    for junc_node in junction_nodes:
        for idx in range(len(junc_node.vessel_info[0]['outlet_vessels'])):
            if aT[counter] > -10: # physical
                zerod_lpn.change_junction_outlet(junction_id_or_name=junc_node.id, which=idx, R = aT[counter] * jcs[counter])
            counter += 1
            
    # Split Constant according to Murrays law into proximal resistance
    
    def load_area_file(area_filepath):
        ''' loads a capinfo file
        '''
        with open(area_filepath, 'r') as afile:
            areas = {}
            afile.readline() # ignore first comment line
            for line in afile:
                line = line.rstrip().split()
                areas[line[0]] = float(line[1])
        return areas
    
    def Rpi(Ai, A, Rp):
        return (A / Ai) * Rp
    
    def split_rpa_lpa(areas):
        ''' splits areas between lpa and rpa areas '''
        lpa_areas = {}
        rpa_areas = {}
        
        validate_caps(areas)
        
        for name, area in areas.items():
            if 'lpa' in name.lower():
                lpa_areas[name] = area
            elif 'rpa' in name.lower():
                rpa_areas[name] = area
        return lpa_areas, rpa_areas

    def validate_caps(areas):
        ''' confirm that all caps have either lpa or rpa in them'''
        names = list(areas.keys())
        for name in names:
            if 'lpa' not in name.lower() and 'rpa' not in name.lower():
                raise ValueError('Unable to identify RPA vs. LPA caps: please rename caps to contain lpa or rpa')
        return 


    areas= load_area_file(M['workspace']['capinfo'])
    del areas[M['metadata']['inlet']]
    # lpa_areas, rpa_areas = split_rpa_lpa(areas)
    # if side == 'LPA':
    #     areas = lpa_areas
    # elif side == 'RPA':
    #     areas = rpa_areas
    
    # print(areas)
    A = sum(list(areas.values()))
    resistances = []
    # add resistances
    global_const = aT[-1] * m2d(1) / zerod_lpn.inflow.mean_inflow
    for name, bc in zerod_lpn.bc_data.items():
        # if bc['face_name'] in areas:
        add_r = Rpi(areas[bc['face_name']], A, global_const)
        print(add_r)
        resistances.append(add_r)
        bc['bc_values']['Rp'] += add_r

    # save the lpn.
    zerod_lpn.update()
    
    
    
    
    


if __name__ == '__main__':
    
    parser = argparse.ArgumentParser(description="Perform a linear optimization on the branches")
    parser.add_argument("-i", dest = 'config', help = 'config.yaml file')
    
    args = parser.parse_args()
    
    
    M = Manager(args.config)
    
    zerod_file = M['workspace']['lpn']
    threed_file = M['workspace']['3D']
    
    # get LPN and convert to BVJ
    zerod_lpn = LPN.from_file(zerod_file)
    zerod_lpn.to_cpp(normal = False) # resets to all 0's
    # reloads the rcrts from previously tuned
    rcr = RCR()
    rcr.read_rcrt_file(M['workspace']['rcrt_file'])
    zerod_lpn.update_rcrs(rcr)
    # write to disk
    zerod_lpn.update()
    
    # load centerlines
    threed_c = Centerlines.load_centerlines(threed_file)
    
    linear_transform(zerod_lpn,threed_c, M)