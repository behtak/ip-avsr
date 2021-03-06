import unittest
import numpy as np
import theano.tensor as T
from lasagne.nonlinearities import rectify, linear
from modelzoo import deltanet_majority_vote
from utils.io import save_mat


class TestModelIO(unittest.TestCase):
    def test_load_params(self):
        window = T.iscalar('theta')
        inputs1 = T.tensor3('inputs1', dtype='float32')
        mask = T.matrix('mask', dtype='uint8')
        network = deltanet_majority_vote.load_saved_model('../oulu/results/best_models/1stream_mfcc_w3s3.6.pkl',
                                                          ([500, 200, 100, 50], [rectify, rectify, rectify, linear]),
                                                          (None, None, 91), inputs1, (None, None), mask,
                                                          250, window, 10)
        d = deltanet_majority_vote.extract_encoder_weights(network, ['fc1', 'fc2', 'fc3', 'bottleneck'],
                                                           [('w1', 'b1'), ('w2', 'b2'), ('w3', 'b3'), ('w4', 'b4')])
        b = deltanet_majority_vote.extract_lstm_weights(network, ['f_blstm1', 'b_blstm1'],
                                                        ['flstm', 'blstm'])
        expected_keys = ['w1', 'w2', 'w3', 'w4', 'b1', 'b2', 'b3', 'b4']
        keys = d.keys()
        for k in keys:
            assert k in expected_keys
            assert type(d[k]) == np.ndarray
        save_mat(d, '../oulu/models/oulu_1stream_mfcc_w3s3.mat')


if __name__ == '__main__':
    unittest.main()
