import sys
sys.path.insert(0, '../')
import pickle
import time

from utils.preprocessing import *
from utils.plotting_utils import *
from utils.data_structures import circular_list
from utils.datagen import *

import theano.tensor as T
import theano
from custom_layers.custom import DeltaLayer
from nolearn.lasagne import NeuralNet

import lasagne as las
import numpy as np
from lasagne.layers import InputLayer, DenseLayer, DropoutLayer, LSTMLayer, Gate, ElemwiseSumLayer, SliceLayer
from lasagne.layers import ReshapeLayer, DimshuffleLayer, ConcatLayer
from lasagne.nonlinearities import tanh, linear, sigmoid, rectify
from lasagne.updates import nesterov_momentum, adadelta, sgd, norm_constraint
from lasagne.objectives import squared_error


def load_dbn(path='models/oulu_ae.mat'):
    """
    load a pretrained dbn from path
    :param path: path to the .mat dbn
    :return: pretrained deep belief network
    """
    # create the network using weights from pretrain_nn.mat
    nn = sio.loadmat(path)
    w1 = nn['w1']
    w2 = nn['w2']
    w3 = nn['w3']
    w4 = nn['w4']
    w5 = nn['w5']
    w6 = nn['w6']
    w7 = nn['w7']
    w8 = nn['w8']
    b1 = nn['b1'][0]
    b2 = nn['b2'][0]
    b3 = nn['b3'][0]
    b4 = nn['b4'][0]
    b5 = nn['b5'][0]
    b6 = nn['b6'][0]
    b7 = nn['b7'][0]
    b8 = nn['b8'][0]

    layers = [
        (InputLayer, {'name': 'input', 'shape': (None, 1144)}),
        (DenseLayer, {'name': 'l1', 'num_units': 2000, 'nonlinearity': sigmoid, 'W': w1, 'b': b1}),
        (DenseLayer, {'name': 'l2', 'num_units': 1000, 'nonlinearity': sigmoid, 'W': w2, 'b': b2}),
        (DenseLayer, {'name': 'l3', 'num_units': 500, 'nonlinearity': sigmoid, 'W': w3, 'b': b3}),
        (DenseLayer, {'name': 'l4', 'num_units': 50, 'nonlinearity': linear, 'W': w4, 'b': b4}),
        (DenseLayer, {'name': 'l5', 'num_units': 500, 'nonlinearity': sigmoid, 'W': w5, 'b': b5}),
        (DenseLayer, {'name': 'l6', 'num_units': 1000, 'nonlinearity': sigmoid, 'W': w6, 'b': b6}),
        (DenseLayer, {'name': 'l7', 'num_units': 2000, 'nonlinearity': sigmoid, 'W': w7, 'b': b7}),
        (DenseLayer, {'name': 'output', 'num_units': 1144, 'nonlinearity': linear, 'W': w8, 'b': b8}),
    ]

    dbn = NeuralNet(
        layers=layers,
        max_epochs=30,
        objective_loss_function=squared_error,
        update=nesterov_momentum,
        regression=True,
        verbose=1,
        update_learning_rate=0.001,
        update_momentum=0.05,
        objective_l2=0.005,
    )
    return dbn


def extract_encoder(dbn):
    dbn_layers = dbn.get_all_layers()
    encoder = NeuralNet(
        layers=[
            (InputLayer, {'name': 'input', 'shape': dbn_layers[0].shape}),
            (DenseLayer, {'name': 'l1', 'num_units': dbn_layers[1].num_units, 'nonlinearity': sigmoid,
                          'W': dbn_layers[1].W, 'b': dbn_layers[1].b}),
            (DenseLayer, {'name': 'l2', 'num_units': dbn_layers[2].num_units, 'nonlinearity': sigmoid,
                          'W': dbn_layers[2].W, 'b': dbn_layers[2].b}),
            (DenseLayer, {'name': 'l3', 'num_units': dbn_layers[3].num_units, 'nonlinearity': sigmoid,
                          'W': dbn_layers[3].W, 'b': dbn_layers[3].b}),
            (DenseLayer, {'name': 'l4', 'num_units': dbn_layers[4].num_units, 'nonlinearity': linear,
                          'W': dbn_layers[4].W, 'b': dbn_layers[4].b}),
        ],
        update=nesterov_momentum,
        update_learning_rate=0.001,
        update_momentum=0.5,
        objective_l2=0.005,
        verbose=1,
        regression=True
    )
    encoder.initialize()
    return encoder


def configure_theano():
    theano.config.floatX = 'float32'
    sys.setrecursionlimit(10000)


def print_layer_shape(layer):
    print('[L] {}: {}'.format(layer.name, las.layers.get_output_shape(layer)))


def print_network(network):
    layers = las.layers.get_all_layers(network)
    for layer in layers:
        print_layer_shape(layer)


def split_data(X, y, subjects, video_lens, train_ids, test_ids):
    """
    Splits the data into training and testing sets
    :param X: input X
    :param y: target y
    :param subjects: array of video -> subject mapping
    :param video_lens: array of video lengths for each video
    :param train_ids: list of subject ids used for training
    :param test_ids: list of subject ids used for testing
    :return: split data
    """
    # construct a subjects data matrix offset
    X_feature_dim = X.shape[1]
    train_X = np.empty((0, X_feature_dim), dtype='float32')
    test_X = np.empty((0, X_feature_dim), dtype='float32')
    train_y = np.empty((0,), dtype='int')
    test_y = np.empty((0,), dtype='int')
    train_vidlens = np.empty((0,), dtype='int')
    test_vidlens = np.empty((0,), dtype='int')
    train_subjects = np.empty((0,), dtype='int')
    test_subjects = np.empty((0,), dtype='int')
    previous_subject = 1
    subject_video_count = 0
    current_video_idx = 0
    current_data_idx = 0
    populate = False
    for idx, subject in enumerate(subjects):
        if previous_subject == subject:  # accumulate
            subject_video_count += 1
        else:  # populate the previous subject
            populate = True
        if idx == len(subjects) - 1:  # check if it is the last entry, if so populate
            populate = True
        if populate:
            # slice the data into the respective splits
            end_video_idx = current_video_idx + subject_video_count
            subject_data_len = int(np.sum(video_lens[current_video_idx:end_video_idx]))
            end_data_idx = current_data_idx + subject_data_len
            if subject in train_ids:
                train_X = np.concatenate((train_X, X[current_data_idx:end_data_idx]))
                train_y = np.concatenate((train_y, y[current_data_idx:end_data_idx]))
                train_vidlens = np.concatenate((train_vidlens, video_lens[current_video_idx:end_video_idx]))
                train_subjects = np.concatenate((train_subjects, subjects[current_video_idx:end_video_idx]))
            else:
                test_X = np.concatenate((test_X, X[current_data_idx:end_data_idx]))
                test_y = np.concatenate((test_y, y[current_data_idx:end_data_idx]))
                test_vidlens = np.concatenate((test_vidlens, video_lens[current_video_idx:end_video_idx]))
                test_subjects = np.concatenate((test_subjects, subjects[current_video_idx:end_video_idx]))
            previous_subject = subject
            current_video_idx = end_video_idx
            current_data_idx = end_data_idx
            subject_video_count = 1
            populate = False
    return train_X, train_y, train_vidlens, train_subjects, test_X, test_y, test_vidlens, test_subjects


def read_data_split_file(path, sep=','):
    with open(path) as f:
        subjects = f.readline().split(sep)
        subjects = [int(s) for s in subjects]
    return subjects


def create_pretrained_encoder(weights, biases, incoming):
    l_1 = DenseLayer(incoming, 2000, W=weights[0], b=biases[0], nonlinearity=sigmoid, name='fc1')
    l_2 = DenseLayer(l_1, 1000, W=weights[1], b=biases[1], nonlinearity=sigmoid, name='fc2')
    l_3 = DenseLayer(l_2, 500, W=weights[2], b=biases[2], nonlinearity=sigmoid, name='fc3')
    l_4 = DenseLayer(l_3, 50, W=weights[3], b=biases[3], nonlinearity=linear, name='encoder')
    return l_4


def create_end_to_end_model(dbn, input_shape, input_var, mask_shape, mask_var,
                            lstm_size=250, win=T.iscalar('theta)')):

    dbn_layers = dbn.get_all_layers()
    weights = []
    biases = []
    weights.append(dbn_layers[1].W.astype('float32'))
    weights.append(dbn_layers[2].W.astype('float32'))
    weights.append(dbn_layers[3].W.astype('float32'))
    weights.append(dbn_layers[4].W.astype('float32'))
    biases.append(dbn_layers[1].b.astype('float32'))
    biases.append(dbn_layers[2].b.astype('float32'))
    biases.append(dbn_layers[3].b.astype('float32'))
    biases.append(dbn_layers[4].b.astype('float32'))

    l_in = InputLayer(input_shape, input_var, 'input')
    l_mask = InputLayer(mask_shape, mask_var, 'mask')

    symbolic_batchsize = l_in.input_var.shape[0]
    symbolic_seqlen = l_in.input_var.shape[1]

    print_layer_shape(l_in)
    l_reshape1 = ReshapeLayer(l_in, (-1, input_shape[-1]), name='reshape1')
    print_layer_shape(l_reshape1)
    # l_encoder = create_fc_encoder(l_reshape1)
    l_encoder = create_pretrained_encoder(weights, biases, l_reshape1)
    print_layer_shape(l_encoder)
    encoder_len = las.layers.get_output_shape(l_encoder)[-1]
    l_reshape2 = ReshapeLayer(l_encoder, (symbolic_batchsize, symbolic_seqlen, encoder_len), name='reshape2')
    print_layer_shape(l_reshape2)
    l_delta = DeltaLayer(l_reshape2, win, name='delta')
    print_layer_shape(l_delta)

    gate_parameters = Gate(
        W_in=las.init.Orthogonal(), W_hid=las.init.Orthogonal(),
        b=las.init.Constant(0.))
    cell_parameters = Gate(
        W_in=las.init.Orthogonal(), W_hid=las.init.Orthogonal(),
        # Setting W_cell to None denotes that no cell connection will be used.
        W_cell=None, b=las.init.Constant(0.),
        # By convention, the cell nonlinearity is tanh in an LSTM.
        nonlinearity=tanh)

    N_HIDDEN = lstm_size
    l_lstm = LSTMLayer(
        l_delta, N_HIDDEN,
        # We need to specify a separate input for masks
        mask_input=l_mask,
        # Here, we supply the gate parameters for each gate
        ingate=gate_parameters, forgetgate=gate_parameters,
        cell=cell_parameters, outgate=gate_parameters,
        # We'll learn the initialization and use gradient clipping
        learn_init=True, grad_clipping=5., name='f_lstm1')
    print_layer_shape(l_lstm)

    # The "backwards" layer is the same as the first,
    # except that the backwards argument is set to True.
    l_lstm_back = LSTMLayer(
        l_in, N_HIDDEN, ingate=gate_parameters,
        mask_input=l_mask, forgetgate=gate_parameters,
        cell=cell_parameters, outgate=gate_parameters,
        learn_init=True, grad_clipping=5., backwards=True, name='b_lstm1')
    print_layer_shape(l_lstm_back)

    # We'll combine the forward and backward layer output by summing.
    # Merge layers take in lists of layers to merge as input.
    l_sum1 = ElemwiseSumLayer([l_lstm, l_lstm_back], name='sum1')
    print_layer_shape(l_sum1)

    l_forward_slice1 = SliceLayer(l_sum1, -1, 1, name='slice1')
    print_layer_shape(l_forward_slice1)

    # Now, we can apply feed-forward layers as usual.
    # We want the network to predict a classification for the sequence,
    # so we'll use a the number of classes.
    l_out = DenseLayer(
        l_forward_slice1, num_units=10, nonlinearity=las.nonlinearities.softmax, name='output')
    print_layer_shape(l_out)

    return l_out


def evaluate_model(X_val, y_val, mask_val, window_size, eval_fn):
    """
    Evaluate a lstm model
    :param X_val: validation inputs
    :param y_val: validation targets
    :param mask_val: input masks for variable sequences
    :param window_size: size of window for computing delta coefficients
    :param eval_fn: evaluation function
    :return: classification rate, confusion matrix
    """
    output = eval_fn(X_val, mask_val, window_size)
    no_gps = output.shape[1]
    confusion_matrix = np.zeros((no_gps, no_gps), dtype='int')

    ix = np.argmax(output, axis=1)
    c = ix == y_val
    classification_rate = np.sum(c == True) / float(len(c))

    # construct the confusion matrix
    for i, target in enumerate(y_val):
        confusion_matrix[target, ix[i]] += 1

    return classification_rate, confusion_matrix


def main():
    configure_theano()
    data = load_mat_file('data/allMouthROIsResized_frontal.mat')

    # 53 subjects, 70 utterances, 5 view angles
    # s[x]_v[y]_u[z].mp4
    # resized, height, width = (26, 44)
    # ['dataMatrix', 'targetH', 'targetsPerVideoVec', 'videoLengthVec', '__header__', 'targetsVec',
    # '__globals__', 'iterVec', 'filenamesVec', 'dataMatrixCells', 'subjectsVec', 'targetW', '__version__']

    print(data.keys())
    X = data['dataMatrix'].astype('float32')
    y = data['targetsVec'].astype('int32')
    y = y.reshape((len(y),))
    uniques = np.unique(y)
    print('number of classifications: {}'.format(len(uniques)))
    subjects = data['subjectsVec'].astype('int')
    subjects = subjects.reshape((len(subjects),))
    video_lens = data['videoLengthVec'].astype('int')
    video_lens = video_lens.reshape((len(video_lens,)))

    train_subject_ids = read_data_split_file('data/train_val.txt')
    test_subject_ids = read_data_split_file('data/test.txt')
    print(train_subject_ids)
    print(test_subject_ids)
    train_X, train_y, train_vidlens, train_subjects, test_X, test_y, test_vidlens, test_subjects = \
        split_data(X, y, subjects, video_lens, train_subject_ids, test_subject_ids)

    assert train_X.shape[0] + test_X.shape[0] == len(X)
    assert train_y.shape[0] + test_y.shape[0] == len(y)
    assert train_vidlens.shape[0] + test_vidlens.shape[0] == len(video_lens)
    assert train_subjects.shape[0] + test_subjects.shape[0] == len(subjects)

    train_X = normalize_input(train_X, centralize=True)
    test_X = normalize_input(test_X, centralize=True)

    finetune = False
    if finetune:
        dbn = load_dbn()
        dbn.initialize()
        dbn.fit(train_X, train_X)

    save = False
    if save:
        pickle.dump(dbn, open('models/oulu_dbn_finetune.dat', 'wb'))

    load = True
    if load:
        print('loading pre-trained encoding layers...')
        dbn = pickle.load(open('models/oulu_dbn_finetune.dat', 'rb'))
        dbn.initialize()

    # IMPT: the encoder was trained with fortan ordered images, so to visualize
    # convert all the images to C order using reshape_images_order()
    # output = dbn.predict(test_X)
    # test_X = reshape_images_order(test_X, (26, 44))
    # output = reshape_images_order(output, (26, 44))
    # visualize_reconstruction(test_X[:36, :], output[:36, :], shape=(26, 44))

    window = T.iscalar('theta')
    inputs = T.tensor3('inputs', dtype='float32')
    mask = T.matrix('mask', dtype='uint8')
    targets = T.ivector('targets')

    print('constructing end to end model...')
    network = create_end_to_end_model(dbn, (None, None, 1144), inputs,
                                      (None, None), mask, 250, window)
    print('compiling model...')
    predictions = las.layers.get_output(network, deterministic=False)
    all_params = las.layers.get_all_params(network, trainable=True)
    cost = T.mean(las.objectives.categorical_crossentropy(predictions, targets))
    updates = las.updates.adadelta(cost, all_params)

    use_max_constraint = False
    if use_max_constraint:
        MAX_NORM = 4
        for param in las.layers.get_all_params(network, regularizable=True):
            if param.ndim > 1:  # only apply to dimensions larger than 1, exclude biases
                updates[param] = norm_constraint(param, MAX_NORM * las.utils.compute_norms(param.get_value()).mean())

    train = theano.function(
        [inputs, targets, mask, window],
        cost, updates=updates, allow_input_downcast=True)
    compute_train_cost = theano.function([inputs, targets, mask, window], cost, allow_input_downcast=True)

    test_predictions = las.layers.get_output(network, deterministic=True)
    test_cost = T.mean(las.objectives.categorical_crossentropy(test_predictions, targets))
    compute_test_cost = theano.function(
        [inputs, targets, mask, window], test_cost, allow_input_downcast=True)

    val_fn = theano.function([inputs, mask, window], test_predictions, allow_input_downcast=True)

    # We'll train the network with 10 epochs of 30 minibatches each
    print('begin training...')
    cost_train = []
    cost_val = []
    class_rate = []
    NUM_EPOCHS = 30
    EPOCH_SIZE = 120
    BATCH_SIZE = 10
    WINDOW_SIZE = 9
    STRIP_SIZE = 3
    MAX_LOSS = 0.2
    VALIDATION_WINDOW = 4
    val_window = circular_list(VALIDATION_WINDOW)
    train_strip = np.zeros((STRIP_SIZE,))

    datagen = gen_lstm_batch_random(train_X, train_y, train_vidlens, batchsize=BATCH_SIZE)
    val_datagen = gen_lstm_batch_random(test_X, test_y, test_vidlens,
                                        batchsize=len(test_vidlens))

    # We'll use this "validation set" to periodically check progress
    X_val, y_val, mask_val = next(val_datagen)

    def early_stop(cost_window):
        if len(cost_window) < 2:
            return False
        else:
            curr = cost_window[0]
            for idx, cost in enumerate(cost_window):
                if curr < cost or idx == 0:
                    curr = cost
                else:
                    return False
            return True

    for epoch in range(NUM_EPOCHS):
        time_start = time.time()
        for _ in range(EPOCH_SIZE):
            X, y, m = next(datagen)
            train(X, y, m, WINDOW_SIZE)
        cost = compute_train_cost(X, y, m, WINDOW_SIZE)
        val_cost = compute_test_cost(X_val, y_val, mask_val, WINDOW_SIZE)
        cost_train.append(cost)
        cost_val.append(val_cost)
        train_strip[epoch % STRIP_SIZE] = cost
        val_window.push(val_cost)

        gl = 100 * (cost_val[-1] / np.min(cost_val) - 1)
        pk = 1000 * (np.sum(train_strip) / (STRIP_SIZE * np.min(train_strip)) - 1)
        pq = gl / pk

        cr, _ = evaluate_model(X_val, y_val, mask_val, WINDOW_SIZE, val_fn)
        class_rate.append(cr)

        print("Epoch {} train cost = {}, validation cost = {}, "
              "generalization loss = {:.3f}, GQ = {:.3f}, classification rate = {:.3f} ({:.1f}sec)"
              .format(epoch + 1, cost_train[-1], cost_val[-1], gl, pq, cr, time.time() - time_start))

        if epoch >= VALIDATION_WINDOW and early_stop(val_window):
            break

    cr, conf = evaluate_model(X_val, y_val, mask_val, WINDOW_SIZE, val_fn)

    phrases = ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7', 'p8', 'p9', 'p10']

    print('Final Model')
    print('classification rate: {}'.format(cr))
    print('confusion matrix: ')
    plot_confusion_matrix(conf, phrases, fmt='grid')
    plot_validation_cost(cost_train, cost_val, class_rate)


if __name__== '__main__':
    main()