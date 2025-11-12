import re

class PIIFilter:
    """Filter personally identifiable information from OCR text"""
    
    @staticmethod
    def remove_pii(text):
        """Remove PII but keep medical information"""
        filtered_text = text
        
        # Remove phone numbers
        filtered_text = re.sub(r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b', '[PHONE_REMOVED]', filtered_text)
        filtered_text = re.sub(r'\(\d{3}\)\s?\d{3}[-.\s]?\d{4}', '[PHONE_REMOVED]', filtered_text)
        
        # Remove emails
        filtered_text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL_REMOVED]', filtered_text)
        
        # Remove DOB
        filtered_text = re.sub(r'\bDOB[:\s]+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', '[DOB_REMOVED]', filtered_text, flags=re.IGNORECASE)
        filtered_text = re.sub(r'\bDate of Birth[:\s]+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', '[DOB_REMOVED]', filtered_text, flags=re.IGNORECASE)
        
        # Remove SSN
        filtered_text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN_REMOVED]', filtered_text)
        
        # Remove MRN/Patient ID
        filtered_text = re.sub(r'\b(?:MRN|Patient ID|Medical Record)[:\s]+[\w\d-]+\b', '[ID_REMOVED]', filtered_text, flags=re.IGNORECASE)
        
        # Remove addresses
        filtered_text = re.sub(r'\b\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct)\b', '[ADDRESS_REMOVED]', filtered_text, flags=re.IGNORECASE)
        
        # Remove zip codes
        filtered_text = re.sub(r'\b\d{5}(?:-\d{4})?\b', '[ZIP_REMOVED]', filtered_text)
        
        # Remove names
        filtered_text = re.sub(r'\b(?:Patient Name|Name|Patient)[:\s]+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', '[NAME_REMOVED]', filtered_text)
        
        return filtered_text
