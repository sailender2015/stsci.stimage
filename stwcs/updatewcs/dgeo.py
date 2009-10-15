import pyfits
from pytools import fileutil
from stwcs import utils
import numpy as np

class DGEOCorr(object):
    """
    Purpose
    =======
    Defines a Lookup table prior distortion correction as per WCS paper IV.
    It uses a reference file defined by the DGEOFILE keyword in the primary header.
    
    Algorithm
    =========
    - Using extensions in the reference file create a WCSDVARR extension 
      and add it to the file object.
    - Add record-valued keywords which describe the lookup tables to the 
      science extension header
    - Add a keyword 'DGEOFILE' to the science extension header, whose
      value is the reference file used to create the WCSVARR extension
    
    If WCSDVARR extensions exist, subsequent updates will overwrite them. 
    If not, they will be added to the file object.
    
    It is assumed that the DGEO reference files were created to work with IDC tables
    but will be applied with SIP coefficients. A transformation is applied to correct 
    for the fact that the lookup tables will be applied before the first order coefficients
    which are in the CD matrix when the SIP convention is used.
    """
    
    def updateWCS(cls, fobj):
        """
        :Parameters:
        `fobj`: pyfits object
                Science file, for which a distortion correction in a DGEOFILE is available
                
        """
        assert isinstance(fobj, pyfits.NP_pyfits.HDUList)
        cls.applyDgeoCorr(fobj)
        dgfile = fobj[0].header['DGEOFILE']
        
        new_kw = {'DGEOEXT': dgfile}
        return new_kw
    
    updateWCS = classmethod(updateWCS)        

    def applyDgeoCorr(cls, fobj):
        """
        For each science extension in a pyfits file object:
            - create a WCSDVARR extension
            - update science header
            - add/update DGEOEXT keyword
        """
        dgfile = fileutil.osfn(fobj[0].header['DGEOFILE'])
        # Map WCSDVARR EXTVER numbers to extension numbers
        wcsdvarr_ind = cls.getWCSIndex(fobj)
        for ext in fobj:
            try:
                extname = ext.header['EXTNAME'].lower()
            except KeyError:
                continue
            if extname == 'sci':
                extversion = ext.header['EXTVER']
                ccdchip = cls.get_ccdchip(fobj, extversion)
                binned = utils.getBinning(fobj, extversion)
                header = ext.header
                # get the data arrays from the reference file and transform them for use with SIP
                dx,dy = cls.getData(dgfile, ccdchip)
                ndx, ndy = cls.transformData(header, dx,dy)
                # Determine EXTVER for the WCSDVARR extension from the DGEO file (EXTNAME, EXTVER) kw.
                # This is used to populate DPj.EXTVER kw
                wcsdvarr_x_version = 2 * extversion -1
                wcsdvarr_y_version = 2 * extversion 
                
                for ename in zip(['DX', 'DY'], [wcsdvarr_x_version,wcsdvarr_y_version],[ndx, ndy]):
                    cls.addSciExtKw(header, wdvarr_ver=ename[1], dgeo_name=ename[0])
                    hdu = cls.createDgeoHDU(header, dgeofile=dgfile, \
                        wdvarr_ver=ename[1], dgeo_name=ename[0], data=ename[2],ccdchip=ccdchip, binned=binned)
                    if wcsdvarr_ind:
                        fobj[wcsdvarr_ind[ename[1]]] = hdu
                    else:
                        fobj.append(hdu)
        
        
    applyDgeoCorr = classmethod(applyDgeoCorr)
              
    def getWCSIndex(cls, fobj):
       
        """
        If fobj has WCSDVARR extensions: 
            returns a mapping of their EXTVER kw to file object extension numbers
        if fobj does not have WCSDVARR extensions:
            an empty dictionary is returned
        """
        wcsd = {}
        for e in range(len(fobj)):
            try:
                ename = fobj[e].header['EXTNAME']
            except KeyError:
                continue
            if ename == 'WCSDVARR':
                wcsd[fobj[e].header['EXTVER']] = e
        
        return wcsd
        
    getWCSIndex = classmethod(getWCSIndex)
    
    def addSciExtKw(cls, hdr, wdvarr_ver=None, dgeo_name=None):
        """
        Adds kw to sci extension to define WCSDVARR lookup table extensions
        
        """
        if dgeo_name =='DX':
            j=1
        else:
            j=2
        
        cperror = 'CPERROR%s' %j
        cpdis = 'CPDIS%s' %j
        dpext = 'DP%s.' %j + 'EXTVER'
        dpnaxes = 'DP%s.' %j +'NAXES'
        dpaxis1 = 'DP%s.' %j+'AXIS.1'
        dpaxis2 = 'DP%s.' %j+'AXIS.2'
        keys = [cperror, cpdis, dpext, dpnaxes, dpaxis1, dpaxis2]
        values = {cperror: 0.0, cpdis: 'Lookup',  dpext: wdvarr_ver, dpnaxes: 2,
                dpaxis1: 1, dpaxis2: 2}
                
        comments = {cperror: 'Maximum error of dgeo correction for axis %s' % (wdvarr_ver/2), 
                    cpdis: 'Prior distortion funcion type',  
                    dpext: 'Version number of WCSDVARR extension containing lookup distortion table', 
                    dpnaxes: 'Number of independent variables in distortion function',
                    dpaxis1: 'Axis number of the jth independent variable in a distortion function', 
                    dpaxis2: 'Axis number of the jth independent variable in a distortion function'
                    }
        
        for key in keys:
            hdr.update(key=key, value=values[key], comment=comments[key], before='HISTORY')
        
    addSciExtKw = classmethod(addSciExtKw)
    
    def getData(cls,dgfile, ccdchip):
        """
        Get the data arrays from the reference DGEO files
        Make sure 'CCDCHIP' in the dgeo file matches "CCDCHIP' in the science file.
        """
        dgf = pyfits.open(dgfile)
        for ext in dgf:
            dgextname  = ext.header.get('EXTNAME',"")
            dgccdchip  = ext.header.get('CCDCHIP',0)
            if dgextname == 'DX' and dgccdchip == ccdchip:
                xdata = ext.data.copy()
                continue
            elif dgextname == 'DY' and dgccdchip == ccdchip:
                ydata = ext.data.copy()
                continue
            else:
                continue
        dgf.close()
        return xdata, ydata
    getData = classmethod(getData)
    
    def transformData(cls, header, dx, dy):
        """
        Transform the DGEO data arrays for use with SIP
        """
        coeffs = cls.getCoeffs(header)
        idcscale = header['IDCSCALE']
        sclcoeffs = np.linalg.inv(coeffs/idcscale)
        ndx, ndy = np.dot(sclcoeffs, [dx.ravel(), dy.ravel()])
        ndx.shape = dx.shape
        ndy.shape=dy.shape
        return ndx, ndy
    transformData = classmethod(transformData)
    
    def getCoeffs(cls, header):
        """
        Return a matrix of the first order IDC coefficients.
        """
        try:
            ocx10 = header['OCX10']
            ocx11 = header['OCX11']
            ocy10 = header['OCY10']
            ocy11 = header['OCY11']
        except AttributeError:
            print 'First order IDCTAB coefficients are not available.\n'
            print 'Cannot convert SIP to IDC coefficients.\n'
            return None
        return np.array([[ocx11, ocx10], [ocy11,ocy10]], dtype=np.float32)
    
    getCoeffs = classmethod(getCoeffs)
    
    def createDgeoHDU(cls, sciheader, dgeofile=None, wdvarr_ver=1, dgeo_name=None,data = None, ccdchip=1, binned=1):
        """
        Creates an HDU to be added to the file object.
        """
        hdr = cls.createDgeoHdr(sciheader, dgeofile=dgeofile, wdvarr_ver=wdvarr_ver, dgeoname=dgeo_name, ccdchip=ccdchip, binned=binned)
        hdu=pyfits.ImageHDU(header=hdr, data=data)
        return hdu
    
    createDgeoHDU = classmethod(createDgeoHDU)
    
    def createDgeoHdr(cls, sciheader, dgeofile, wdvarr_ver, dgeoname, ccdchip, binned):
        """
        Creates a header for the WCSDVARR extension based on the DGEO reference file 
        and sci extension header. The goal is to always work in image coordinates
        (also for subarrays and binned images. The WCS for the WCSDVARR extension 
        i ssuch that a full size dgeo table is created and then shifted or scaled 
        if the science image is a subarray or binned image.
        """
        dgf = pyfits.open(dgeofile)
        for ext in dgf:
            dgextname = ext.header.get('EXTNAME', "")
            dgccdchip = ext.header.get('CCDCHIP', 0)
            if dgextname == dgeoname and dgccdchip == ccdchip:
                dgeo_header = ext.header
                break
            else:
                continue
        dgf.close()
        
        #LTV1/2 are needed to map correctly dgeo files into subarray coordinates
        ltv1 = sciheader.get('LTV1', 0.0)
        ltv2 = sciheader.get('LTV2', 0.0)
        
        # This is the size of the full dgeo chip
        # recorded in the small dgeo table and needed in order to map to full dgeo
        # chip coordinates.
        chip_size1 = dgeo_header['ONAXIS1'] 
        chip_size2 = dgeo_header['ONAXIS2'] 
        ccdchip = dgeo_header['CCDCHIP']
        
        naxis1 = dgeo_header['naxis1']
        naxis2 = dgeo_header['naxis2'] 
        extver = dgeo_header['extver'] 
        crpix1 = naxis1/2.
        crpix2 = naxis2/2.
        cdelt1 = float(chip_size1) / (naxis1  * binned)
        cdelt2 = float(chip_size2) / (naxis2 * binned) 
        
        # CRVAL1/2 of the small dgeo table is the shifted center of the full
        # dgeo chip.
        crval1 = (chip_size1/2. + ltv1) / binned
        crval2 = (chip_size2/2. + ltv2) / binned
        
        keys = ['XTENSION','BITPIX','NAXIS','NAXIS1','NAXIS2',
              'EXTNAME','EXTVER','PCOUNT','GCOUNT','CCDCHIP', 'CRPIX1',
                        'CDELT1','CRVAL1','CRPIX2','CDELT2','CRVAL2']
                        
        comments = {'XTENSION': 'Image extension',
                    'BITPIX': 'IEEE floating point',
                    'NAXIS': 'Number of axes',
                    'NAXIS1': 'Number of image columns',
                    'NAXIS2': 'Number of image rows',
                    'EXTNAME': 'WCS distortion array',
                    'EXTVER': 'Distortion array version number',
                    'PCOUNT': 'Special data area of size 0',
                    'GCOUNT': 'One data group',
                    'CCDCHIP': 'CCDCHIP number',
                    'CRPIX1': 'Distortion array reference pixel',
                    'CDELT1': 'Grid step size in first coordinate',
                    'CRVAL1': 'Image array pixel coordinate',
                    'CRPIX2': 'Distortion array reference pixel',
                    'CDELT2': 'Grid step size in second coordinate',
                    'CRVAL2': 'Image array pixel coordinate'}
        
        values = {'XTENSION': 'IMAGE',
                'BITPIX': -32,
                'NAXIS': 2,
                'NAXIS1': naxis1,
                'NAXIS2': naxis2,
                'EXTNAME': 'WCSDVARR',
                'EXTVER':  wdvarr_ver,
                'PCOUNT': 0,
                'GCOUNT': 1,
                'CCDCHIP': ccdchip,
                'CRPIX1': crpix1,
                'CDELT1': cdelt1,
                'CRVAL1': crval1,
                'CRPIX2': crpix2,
                'CDELT2': cdelt2,
                'CRVAL2': crval2
                }
                    
        
        cdl = pyfits.CardList()
        for c in keys:
            cdl.append(pyfits.Card(key=c, value=values[c], comment=comments[c]))

        hdr = pyfits.Header(cards=cdl)
        return hdr
    
    createDgeoHdr = classmethod(createDgeoHdr)
    
    def get_ccdchip(cls, fobj, extver):
        """
        Currently this is only needed for ACS, since this is the only instrument
        with DGEO correction. The rest is here for completeness.
        """
        dgfile = fileutil.osfn(fobj[0].header['DGEOFILE'])
        if fobj[0].header['INSTRUME'] == 'ACS':
            return fobj['SCI', extver].header['CCDCHIP']
        elif obj[0].header['INSTRUME'] == 'WFC3':
            return  fobj['SCI', extver].header['CCDCHIP']
        elif fobj[0].header['INSTRUME'] == 'WFPC2':
            return fobj[0].header['DETECTOR']
        elif fobj[0].header['INSTRUME'] == 'STIS':
            return fobj['SCI', extver].header['DETECTOR']
        elif fobj[0].header['INSTRUME'] == 'NICMOS':
            return fobj['SCI', extver].header['CAMERA']
        
        
    get_ccdchip = classmethod(get_ccdchip)
    