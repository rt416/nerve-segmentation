import os
import sys
import numpy as np
import theano
import theano.tensor as T
from theano.ifelse import ifelse
import time
import math
import lasagne 
import scipy.misc
import argparse
import glob
import cv2
from skimage import measure
import img_augmentation as aug

#import config as c
import model
import misc


def postprocess(pred, minsize=5000, dilations=2, sigma=5):
    predthr = (pred[0]>=0.5)

    lcc = measure.label(predthr)
    num_lccs = np.max(lcc)
    if num_lccs>1:
        bestlcc=-1
        bestlccscore=-1
        for l in range(num_lccs):
            score = ((lcc == l+1)*pred).sum()
            if score > bestlccscore:
                bestlccscore=score
                bestlcc = l+1
        lcc=lcc.reshape(pred.shape)
        predthr=(lcc==bestlcc)

    if predthr.sum() <= minsize:
        predthr = np.zeros(pred.shape)

    return predthr


def join_models_cv(version=0, test_dir='test', results_dir='submit', seed=1234, minsize=5000):
    c = misc.load_config(version)

    start_time = time.clock()   
    print_dir = os.path.join(results_dir, '{}'.format(version))
    join_dirs = print_dir+"/fold*seed"+str(seed)
    if seed == "*":
        join_dir = print_dir+"/joined"
    else:
        join_dir = print_dir+"/joined_seed"+str(seed)
    if not os.path.exists(join_dir):
        os.makedirs(join_dir)

    cv_dirs = [o for o in glob.glob(join_dirs) if os.path.isdir(o)]
    print(cv_dirs)
    misc.sort_nicely(cv_dirs)
    num_cvs = len(cv_dirs)
    images = glob.glob(test_dir+'/*'+c.image_ext)
    num_images = len(images)

    premod = 0
    for i in range(num_images):
        img_name = images[i]
        pred_name = os.path.splitext(os.path.basename(img_name))[0] + "_pred" + c.image_ext

        pred = None
        for d in range(num_cvs):
            img_name = cv_dirs[d]+'/'+pred_name
            img = misc.load_image(img_name)
            if d == 0:
                pred = img
            else:
                pred += img
        pred /= num_cvs

        pred = postprocess(pred, minsize=minsize)
        fnpred = os.path.join(join_dir, pred_name)
        scipy.misc.imsave(fnpred, pred.reshape(c.height, c.width) * np.float32(255))

        mod = math.floor(i / num_images * 10) 
        if mod > premod and mod > 0:
            print(str(mod*10)+'%..',end="",flush=True)
        premod = mod

    print("Prediction took {:.3f}s".format(time.clock() - start_time))




def test_model(version=1, test_dir='test', results_dir='submit', fold=1, num_folds=10, minsize=5000, tta_num=1, seed=1234):  
    folddir = misc.get_fold_dir(version, fold, num_folds, seed)
    print_dir = folddir.replace(misc.params_dir+"/", results_dir+"/")
    print("testing cv fold "+str(fold)+" of "+str(num_folds)+" (dir: "+print_dir+")\n")

    start_time = time.clock() 

    # load model config 
    c = misc.load_config(version)

    shape = (1, 1, c.height, c.width)
    orig_shape = shape
    if c.resize:        
        orig_shape = shape
        shape = (1, 1, round(c.height*c.resize), round(c.width*c.resize) ) 
    netshape = (tta_num, shape[1], shape[2], shape[3])

    input_var = T.tensor4('input')
    label_var = T.tensor4('label')

    net = model.network(input_var, netshape, filter_size=c.filter_size, version=c.modelversion, depth=c.depth, num_filters=c.filters, autoencoder=c.autoencoder, autoencoder_dropout=c.autoencoder_dropout)

    output_det = lasagne.layers.get_output(net['output'], deterministic=True)
    predict_model = theano.function(inputs=[input_var], outputs=output_det)

    # load best params
    misc.load_last_params(net['output'], folddir, best=True)

    print_idx = 0

    if not os.path.exists(print_dir):
        os.makedirs(print_dir)

    images = glob.glob(test_dir+'/*'+c.image_ext)
    num_images = len(images)

    premod = 0
    for i in range(num_images):
        img_name = images[i]
        fnpred = os.path.join(print_dir, os.path.splitext(os.path.basename(img_name))[0] +'_pred'+c.image_ext)

        if not os.path.exists(fnpred):
            img = misc.load_image(img_name)
            if c.resize:
                img = cv2.resize(img[0], (shape[3], shape[2]), interpolation=cv2.INTER_CUBIC).reshape(shape)
            else:
                img = img.reshape(shape)

            if tta_num>1:
                pred = aug.test_time_augmentation(img, predict_model, tta_num, orig_shape, c.aug_params)
            else:
                pred = predict_model(img)
                if c.resize:
                    pred = cv2.resize(pred[0][0], (orig_shape[3], orig_shape[2]), interpolation=cv2.INTER_LINEAR).reshape(orig_shape)
            scipy.misc.imsave(fnpred, pred.reshape(c.height, c.width) * np.float32(255))

        mod = math.floor(i / num_images * 10) 
        if mod > premod and mod > 0:
            print(str(mod*10)+'%..',end="",flush=True)
        premod = mod

    print("Prediction took {:.3f}s".format(time.clock() - start_time))


def main(): 
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--version", dest="version",  help="version", default=0.0)
    parser.add_argument("-cv", dest="cv",  help="cv", default=10) 
    parser.add_argument("-fold", dest="fold",  help="fold", default=0)  
    parser.add_argument("-seed", dest="seed",  help="seed", default=1234)

    parser.add_argument("-results", dest="results",  help="results", default='submit')
    parser.add_argument("-test", dest="test",  help="test", default="test")

    parser.add_argument("-ms", "-minsize", dest="minsize",  help="minsize", default=5000) 
    parser.add_argument("-tta", "-tta", dest="tta",  help="tta", default=1) 
    parser.add_argument("--join", dest="join",  help="join", action='store_true')
    parser.add_argument("--join-all", dest="join_all",  help="join_all", action='store_true')
    options = parser.parse_args()

    version = options.version
    cv = int(options.cv)
    fold = int(options.fold)
    results_dir = options.results
    test_dir = options.test
    minsize = int(options.minsize)
    tta = int(options.tta)
    seed = int(options.seed)
    join = int(options.join)
    join_all = int(options.join_all)
    
    print("Arguments:")
    print("----------")
    print(options)
    print()

    if not join_all:
        num_folds = cv
        ifold = 1
        lfold = num_folds+1
        step = 1

        if fold > 0:
            ifold = fold
            lfold = fold+1

        for fold in range(ifold,lfold,step):
                test_model(version, test_dir=test_dir, results_dir=results_dir, fold=fold, num_folds=num_folds, tta_num=tta, seed=seed)

    if join or join_all:
        if join_all:
            seed='*'
        join_models_cv(version, test_dir, results_dir=results_dir, seed=seed, minsize=minsize)


if __name__ == '__main__':
    main()
