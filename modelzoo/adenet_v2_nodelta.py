import theano.tensor as T

import lasagne as las
from lasagne.layers import InputLayer, LSTMLayer, DenseLayer, ConcatLayer, SliceLayer, ReshapeLayer, ElemwiseSumLayer
from lasagne.layers import Gate, DropoutLayer, GlobalPoolLayer
from lasagne.nonlinearities import tanh, sigmoid, linear, rectify, leaky_rectify

from custom.layers import DeltaLayer, AdaptiveElemwiseSumLayer
from modelzoo.pretrained_encoder import create_pretrained_encoder


def create_blstm(l_incoming, l_mask, hidden_units, cell_parameters, gate_parameters, name, use_peepholes=True):

    if cell_parameters is None:
        cell_parameters = Gate()
    if gate_parameters is None:
        gate_parameters = Gate()

    l_lstm = LSTMLayer(
        l_incoming, hidden_units,
        # We need to specify a separate input for masks
        mask_input=l_mask, peepholes=use_peepholes,
        # Here, we supply the gate parameters for each gate
        ingate=gate_parameters, forgetgate=gate_parameters,
        cell=cell_parameters, outgate=gate_parameters,
        # We'll learn the initialization and use gradient clipping
        learn_init=True, grad_clipping=5., name='f_{}'.format(name))

    # The "backwards" layer is the same as the first,
    # except that the backwards argument is set to True.
    l_lstm_back = LSTMLayer(
        l_incoming, hidden_units, ingate=gate_parameters, peepholes=use_peepholes,
        mask_input=l_mask, forgetgate=gate_parameters,
        cell=cell_parameters, outgate=gate_parameters,
        learn_init=True, grad_clipping=5., backwards=True, name='b_{}'.format(name))

    return l_lstm, l_lstm_back


def create_model(ae, s2_ae, input_shape, input_var, mask_shape, mask_var,
                 s2_shape, s2_var, lstm_size=250,
                 output_classes=26, fusiontype='concat', w_init_fn=las.init.Orthogonal(),
                 use_peepholes=True):

    bn_weights, bn_biases, bn_shapes, bn_nonlinearities = ae
    s2_weights, s2_biases, s2_shapes, s2_nonlinearities = s2_ae

    gate_parameters = Gate(
        W_in=w_init_fn, W_hid=w_init_fn,
        b=las.init.Constant(0.))
    cell_parameters = Gate(
        W_in=w_init_fn, W_hid=w_init_fn,
        # Setting W_cell to None denotes that no cell connection will be used.
        W_cell=None, b=las.init.Constant(0.),
        # By convention, the cell nonlinearity is tanh in an LSTM.
        nonlinearity=tanh)

    l_s1 = InputLayer(input_shape, input_var, 's1_im')
    l_mask = InputLayer(mask_shape, mask_var, 'mask')
    l_s2 = InputLayer(s2_shape, s2_var, 's2_im')

    symbolic_batchsize_s1 = l_s1.input_var.shape[0]
    symbolic_seqlen_s1 = l_s1.input_var.shape[1]
    symbolic_batchsize_s2 = l_s2.input_var.shape[0]
    symbolic_seqlen_s2 = l_s2.input_var.shape[1]

    l_reshape1_s1 = ReshapeLayer(l_s1, (-1, input_shape[-1]), name='reshape1_s1')
    l_encoder_s1 = create_pretrained_encoder(l_reshape1_s1, bn_weights, bn_biases, bn_shapes, bn_nonlinearities,
                                              ['fc1_s1', 'fc2_s1', 'fc3_s1', 'bottleneck_s1'])
    s1_len = las.layers.get_output_shape(l_encoder_s1)[-1]

    l_reshape2_s1 = ReshapeLayer(l_encoder_s1,
                                 (symbolic_batchsize_s1, symbolic_seqlen_s1, s1_len),
                                 name='reshape2_s1')

    # s2 images
    l_reshape1_s2 = ReshapeLayer(l_s2, (-1, s2_shape[-1]), name='reshape1_s2')
    l_encoder_s2 = create_pretrained_encoder(l_reshape1_s2, s2_weights, s2_biases, s2_shapes,
                                             s2_nonlinearities,
                                             ['fc1_s2', 'fc2_s2', 'fc3_s2', 'bottleneck_s2'])
    s2_len = las.layers.get_output_shape(l_encoder_s2)[-1]
    l_reshape2_s2 = ReshapeLayer(l_encoder_s2,
                                 (symbolic_batchsize_s2, symbolic_seqlen_s2, s2_len),
                                 name='reshape2_s2')

    l_lstm_s1 = LSTMLayer(
        l_reshape2_s1, int(lstm_size), peepholes=use_peepholes,
        # We need to specify a separate input for masks
        mask_input=l_mask,
        # Here, we supply the gate parameters for each gate
        ingate=gate_parameters, forgetgate=gate_parameters,
        cell=cell_parameters, outgate=gate_parameters,
        # We'll learn the initialization and use gradient clipping
        learn_init=True, grad_clipping=5., name='lstm_s1')

    l_lstm_s2 = LSTMLayer(
        l_reshape2_s2, lstm_size, peepholes=use_peepholes,
        # We need to specify a separate input for masks
        mask_input=l_mask,
        # Here, we supply the gate parameters for each gate
        ingate=gate_parameters, forgetgate=gate_parameters,
        cell=cell_parameters, outgate=gate_parameters,
        # We'll learn the initialization and use gradient clipping
        learn_init=True, grad_clipping=5., name='lstm_s2')

    # We'll combine the forward and backward layer output by summing.
    # Merge layers take in lists of layers to merge as input.
    if fusiontype == 'adasum':
        l_fuse = AdaptiveElemwiseSumLayer([l_lstm_s1, l_lstm_s2], name='adasum1')
    elif fusiontype == 'sum':
        l_fuse = ElemwiseSumLayer([l_lstm_s1, l_lstm_s2], name='sum1')
    elif fusiontype == 'concat':
        l_fuse = ConcatLayer([l_lstm_s1, l_lstm_s2], axis=-1, name='concat')

    f_lstm_agg, b_lstm_agg = create_blstm(l_fuse, l_mask, lstm_size, cell_parameters, gate_parameters, 'lstm_agg')
    l_sum2 = ElemwiseSumLayer([f_lstm_agg, b_lstm_agg], name='sum2')

    # reshape to (num_examples * seq_len, lstm_size)
    l_reshape3 = ReshapeLayer(l_sum2, (-1, lstm_size), name='reshape3')

    # Now, we can apply feed-forward layers as usual.
    # We want the network to predict a classification for the sequence,
    # so we'll use a the number of classes.
    l_softmax = DenseLayer(
        l_reshape3, num_units=output_classes,
        nonlinearity=las.nonlinearities.softmax, name='softmax')

    l_out = ReshapeLayer(l_softmax, (-1, symbolic_seqlen_s1, output_classes), name='output')

    return l_out, l_fuse
